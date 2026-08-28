"""Conservative temporary configuration for one dedicated PACE Ethernet adapter."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass


class NetworkConfigurationError(RuntimeError):
    pass


@dataclass(slots=True)
class NetworkLease:
    interface: str
    address_added: bool
    platform_name: str


def configure_dedicated_adapter(
    address: str = "192.168.10.1", prefix_length: int = 24
) -> NetworkLease:
    system = platform.system()
    if system == "Windows":
        return _configure_windows(address, prefix_length)
    if system == "Linux":
        return _configure_linux(address, prefix_length)
    raise NetworkConfigurationError(
        f"Automatic Ethernet configuration is not supported on {system}."
    )


def restore_dedicated_adapter(lease: NetworkLease | None, address: str = "192.168.10.1") -> None:
    if lease is None or not lease.address_added:
        return
    if lease.platform_name == "Windows":
        script = (
            f"Remove-NetIPAddress -InterfaceIndex {int(lease.interface)} "
            f"-IPAddress '{address}' -Confirm:$false -ErrorAction SilentlyContinue"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
        )
    elif lease.platform_name == "Linux":
        command = ["ip", "address", "delete", f"{address}/24", "dev", lease.interface]
        _run_linux_privileged(command, check=False)


def _configure_windows(address: str, prefix_length: int) -> NetworkLease:
    discovery = r"""
$items = @()
Get-NetAdapter -Physical -ErrorAction Stop | Where-Object Status -eq 'Up' | ForEach-Object {
  $idx = $_.ifIndex
  $ips = @(Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -ne '127.0.0.1' } | Select-Object -ExpandProperty IPAddress)
  $gateways = @(Get-NetRoute -InterfaceIndex $idx -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)
  $items += [pscustomobject]@{ Index=$idx; Name=$_.Name; IPs=$ips; HasGateway=($gateways.Count -gt 0) }
}
$items | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", discovery],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NetworkConfigurationError(result.stderr.strip() or "Cannot inspect Windows adapters")
    try:
        raw = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise NetworkConfigurationError("Cannot parse Windows adapter information") from exc
    adapters = raw if isinstance(raw, list) else [raw]
    for item in adapters:
        ips = item.get("IPs", [])
        if isinstance(ips, str):
            ips = [ips]
        if address in ips:
            return NetworkLease(str(item["Index"]), False, "Windows")
    candidates = [
        item
        for item in adapters
        if not item.get("HasGateway", False)
        and all(str(ip).startswith("169.254.") for ip in (item.get("IPs", []) if isinstance(item.get("IPs", []), list) else [item.get("IPs")]))
    ]
    if len(candidates) != 1:
        raise NetworkConfigurationError(
            f"Expected exactly one safe dedicated Ethernet adapter; found {len(candidates)}. No adapter was modified."
        )
    index = int(candidates[0]["Index"])
    command = (
        f"New-NetIPAddress -InterfaceIndex {index} -IPAddress '{address}' "
        f"-PrefixLength {prefix_length} -ErrorAction Stop | Out-Null"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NetworkConfigurationError(
            result.stderr.strip() or "Administrator privileges are required to configure Ethernet."
        )
    return NetworkLease(str(index), True, "Windows")


def _configure_linux(address: str, prefix_length: int) -> NetworkLease:
    if shutil.which("ip") is None:
        raise NetworkConfigurationError("The Linux 'ip' command is not installed.")
    result = subprocess.run(
        ["ip", "-j", "address", "show", "up"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NetworkConfigurationError(result.stderr.strip() or "Cannot inspect Linux adapters")
    interfaces = json.loads(result.stdout or "[]")
    default_routes = subprocess.run(
        ["ip", "-j", "route", "show", "default"],
        check=False,
        capture_output=True,
        text=True,
    )
    gateway_interfaces = {
        str(route.get("dev", ""))
        for route in json.loads(default_routes.stdout or "[]")
    }
    candidates: list[str] = []
    for item in interfaces:
        name = str(item.get("ifname", ""))
        if name == "lo" or name in gateway_interfaces:
            continue
        if not os.path.exists(f"/sys/class/net/{name}/device"):
            continue
        addresses = [
            entry.get("local", "")
            for entry in item.get("addr_info", [])
            if entry.get("family") == "inet"
        ]
        if address in addresses:
            return NetworkLease(name, False, "Linux")
        if all(str(value).startswith("169.254.") for value in addresses):
            candidates.append(name)
    if len(candidates) != 1:
        raise NetworkConfigurationError(
            f"Expected exactly one safe dedicated Ethernet adapter; found {len(candidates)}. No adapter was modified."
        )
    name = candidates[0]
    _run_linux_privileged(
        ["ip", "address", "add", f"{address}/{prefix_length}", "dev", name],
        check=True,
    )
    return NetworkLease(name, True, "Linux")


def _run_linux_privileged(command: list[str], check: bool) -> None:
    actual = command if os.geteuid() == 0 else ["pkexec", *command]
    if actual[0] == "pkexec" and shutil.which("pkexec") is None:
        raise NetworkConfigurationError(
            "Administrator privileges are required and pkexec is not available. Configure 192.168.10.1/24 manually."
        )
    result = subprocess.run(actual, check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise NetworkConfigurationError(
            result.stderr.strip() or "Failed to configure the dedicated Ethernet adapter."
        )


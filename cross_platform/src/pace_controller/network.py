"""Conservative temporary configuration for one dedicated PACE Ethernet adapter."""

from __future__ import annotations

import ipaddress
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
    route_added: bool = False
    source_address: str = "192.168.10.1"


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
    if lease is None:
        return
    address = lease.source_address or address
    if lease.platform_name == "Windows":
        commands: list[str] = []
        if lease.route_added:
            commands.append(
                f"Remove-NetRoute -InterfaceIndex {int(lease.interface)} "
                "-AddressFamily IPv4 -DestinationPrefix '192.168.10.0/24' "
                "-Confirm:$false -ErrorAction SilentlyContinue"
            )
        if lease.address_added:
            commands.append(
                f"Remove-NetIPAddress -InterfaceIndex {int(lease.interface)} "
                f"-IPAddress '{address}' -Confirm:$false -ErrorAction SilentlyContinue"
            )
        if not commands:
            return
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "; ".join(commands),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    elif lease.platform_name == "Linux" and lease.address_added:
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
    network = ipaddress.ip_network(f"{address}/{prefix_length}", strict=False)
    owners = [item for item in adapters if address in _windows_ips(item)]
    if len(owners) > 1:
        raise NetworkConfigurationError(
            f"Address {address} is present on multiple adapters. No adapter was modified."
        )

    address_added = False
    if owners:
        selected = owners[0]
    else:
        candidates = [
            item
            for item in adapters
            if not item.get("HasGateway", False)
            and all(value.is_link_local for value in _windows_ipv4_addresses(item))
        ]
        if len(candidates) != 1:
            raise NetworkConfigurationError(
                "Expected exactly one safe dedicated Ethernet adapter; "
                f"found {len(candidates)}. No adapter was modified."
            )
        selected = candidates[0]

        conflicts = [
            item
            for item in adapters
            if item is not selected
            and any(value in network for value in _windows_ipv4_addresses(item))
        ]
        if conflicts:
            raise NetworkConfigurationError(
                f"Network {network} is already used by another adapter. No adapter was modified."
            )

        index = int(selected["Index"])
        command = (
            f"New-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv4 "
            f"-IPAddress '{address}' -PrefixLength {prefix_length} "
            "-PolicyStore ActiveStore -ErrorAction Stop | Out-Null"
        )
        result = _run_windows_powershell(command)
        if result.returncode != 0:
            raise NetworkConfigurationError(
                result.stderr.strip()
                or "Administrator privileges are required to configure Ethernet."
            )
        address_added = True

    index = int(selected["Index"])
    destination = str(network)
    prepare = f"""
$temporaryAddress = $null
for ($attempt = 1; $attempt -le 20; $attempt++) {{
  $temporaryAddress = @(Get-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv4 -IPAddress '{address}' -ErrorAction SilentlyContinue |
    Where-Object AddressState -eq 'Preferred')
  if ($temporaryAddress.Count -gt 0) {{ break }}
  Start-Sleep -Milliseconds 500
}}
if ($temporaryAddress.Count -eq 0) {{
  throw 'Windows did not make {address}/{prefix_length} operational on adapter {index}.'
}}
$routes = @(Get-NetRoute -InterfaceIndex {index} -AddressFamily IPv4 -DestinationPrefix '{destination}' -ErrorAction SilentlyContinue)
if ($routes.Count -eq 0) {{
  New-NetRoute -InterfaceIndex {index} -AddressFamily IPv4 -DestinationPrefix '{destination}' -NextHop '0.0.0.0' -RouteMetric 1 -PolicyStore ActiveStore -ErrorAction Stop | Out-Null
  Write-Output 'created'
}} else {{
  Write-Output 'existing'
}}
"""
    result = _run_windows_powershell(prepare)
    if result.returncode != 0:
        if address_added:
            restore_dedicated_adapter(
                NetworkLease(str(index), True, "Windows", source_address=address)
            )
        raise NetworkConfigurationError(
            result.stderr.strip() or "Cannot prepare the Windows route to the PACE."
        )
    route_added = result.stdout.strip().splitlines()[-1:] == ["created"]
    return NetworkLease(str(index), address_added, "Windows", route_added, address)


def _windows_ips(item: dict[str, object]) -> list[str]:
    values = item.get("IPs", [])
    if isinstance(values, str):
        return [values]
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if value]


def _windows_ipv4_addresses(item: dict[str, object]) -> list[ipaddress.IPv4Address]:
    addresses: list[ipaddress.IPv4Address] = []
    for value in _windows_ips(item):
        try:
            addresses.append(ipaddress.IPv4Address(value))
        except ipaddress.AddressValueError:
            continue
    return addresses


def _run_windows_powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
    )


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
            return NetworkLease(name, False, "Linux", source_address=address)
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
    return NetworkLease(name, True, "Linux", source_address=address)


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

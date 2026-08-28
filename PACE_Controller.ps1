[CmdletBinding()]
param(
    [string]$PaceAddress = "192.168.10.2",
    [int]$TcpPort = 5025
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$script:Version = "0.2.0"
$script:Connection = $null
$script:Connected = $false
$script:PollFailures = 0
$script:TemporaryInterfaceIndex = $null
$script:TemporaryInterfaceAlias = $null
$script:OriginalDhcpState = $null
$script:TemporaryRouteCreated = $false
$script:TemporaryIpAddress = "192.168.10.1"
$script:CurrentPressure = [double]::NaN
$script:CurrentTarget = [double]::NaN
$script:CurrentSourcePositive = [double]::NaN
$script:CurrentInLimit = $false
$script:CurrentOutputOn = $false
$script:MinimumSupplyMarginBar = 2.0
$script:SupplyInterlockResetMarginBar = 2.2
$script:SupplyInterlockLatched = $false
$script:RangeMinimum = [double]::NaN
$script:RangeMaximum = [double]::NaN
$script:ClosingHandled = $false
$script:LogPath = Join-Path $PSScriptRoot "PACE_controller_log.txt"
$script:CsvPath = Join-Path $PSScriptRoot "PACE_controller_data.csv"
$script:SettingsPath = Join-Path $PSScriptRoot "PACE_controller_settings.json"
$script:Culture = [System.Globalization.CultureInfo]::InvariantCulture
$script:LeakReferenceDropBar = 0.005
$script:LeakGreenMinutes = 10.0
$script:LeakYellowMinutes = 5.0
$script:LeakOrangeMinutes = 1.0
$script:SampleLeakHistory = @()
$script:SourceLeakHistory = @()
$script:Automation = [pscustomobject]@{
    Active = $false
    Mode = ""
    State = "Idle"
    Steps = @()
    Index = 0
    DwellEnd = [datetime]::MinValue
    WaitDeadline = [datetime]::MinValue
    KeepControlAtEnd = $false
}

function Write-AppLog {
    param([string]$Message)
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"), $Message
    Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8
    if ($null -ne $script:txtLog) {
        $script:txtLog.AppendText($line + [Environment]::NewLine)
        $script:txtLog.SelectionStart = $script:txtLog.TextLength
        $script:txtLog.ScrollToCaret()
    }
}

function Show-Error {
    param([string]$Message)
    Write-AppLog "ERRORE: $Message"
    [System.Windows.Forms.MessageBox]::Show(
        $Message,
        "PACE Controller - errore",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-SafePaceAdapterCandidate {
    $candidates = @()
    $adapters = @(Get-NetAdapter -Physical -ErrorAction Stop | Where-Object {
        $_.Status -eq "Up" -and $_.MediaType -eq "802.3"
    })

    foreach ($adapter in $adapters) {
        $index = $adapter.InterfaceIndex
        $ipInterface = Get-NetIPInterface -InterfaceIndex $index -AddressFamily IPv4 -ErrorAction SilentlyContinue
        $addresses = @(Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 -ErrorAction SilentlyContinue)
        $defaultRoutes = @(Get-NetRoute -InterfaceIndex $index -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue)
        $nonLinkLocal = @($addresses | Where-Object {
            $_.IPAddress -notlike "169.254.*" -and $_.IPAddress -ne "0.0.0.0"
        })
        $addressText = if ($addresses) { $addresses.IPAddress -join "," } else { "nessuno" }
        $dhcpText = if ($ipInterface) { [string]$ipInterface.Dhcp } else { "sconosciuto" }
        Write-AppLog "Scheda '$($adapter.Name)' indice=$index DHCP=$dhcpText IP=$addressText gateway=$($defaultRoutes.Count -gt 0)"

        if ($null -eq $ipInterface) { continue }
        if ($defaultRoutes.Count -gt 0) { continue }
        if ($nonLinkLocal.Count -gt 0) { continue }
        $candidates += $adapter
    }

    if ($candidates.Count -eq 0) {
        throw "Nessuna scheda Ethernet dedicata e sicura trovata. Nessuna scheda e stata modificata."
    }
    if ($candidates.Count -gt 1) {
        throw "Piu schede Ethernet sembrano dedicate ($($candidates.Name -join ', ')). Per sicurezza nessuna scheda e stata modificata."
    }
    return $candidates[0]
}

function Enable-TemporaryPaceNetwork {
    if (-not (Test-IsAdministrator)) {
        throw "Servono i privilegi di amministratore. Avvia Avvia_PACE_Controller.bat e accetta la richiesta UAC."
    }

    $adapter = Get-SafePaceAdapterCandidate
    $index = $adapter.InterfaceIndex
    $conflicts = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
        $_.InterfaceIndex -ne $index -and $_.IPAddress -like "192.168.10.*"
    })
    if ($conflicts.Count -gt 0) {
        throw "La rete 192.168.10.0/24 e gia usata da un'altra scheda. Nessuna modifica eseguita."
    }

    $script:TemporaryInterfaceIndex = $index
    $script:TemporaryInterfaceAlias = $adapter.Name
    $script:OriginalDhcpState = [string](Get-NetIPInterface `
        -InterfaceIndex $index -AddressFamily IPv4 -ErrorAction Stop).Dhcp

    Write-AppLog "Configuro solo '$($adapter.Name)' (indice $index) con 192.168.10.1/24."
    $existing = Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 `
        -IPAddress $script:TemporaryIpAddress -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 `
            -IPAddress $script:TemporaryIpAddress -PrefixLength 24 `
            -PolicyStore ActiveStore -ErrorAction Stop | Out-Null
    }

    $temporaryAddress = $null
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        $temporaryAddress = Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 `
            -IPAddress $script:TemporaryIpAddress -ErrorAction SilentlyContinue
        if ($temporaryAddress -and [string]$temporaryAddress.AddressState -eq "Preferred") { break }
        Start-Sleep -Seconds 1
    }
    if (-not $temporaryAddress) {
        throw "Windows non ha applicato 192.168.10.1 alla scheda Ethernet."
    }

    $routes = @(Get-NetRoute -InterfaceIndex $index -AddressFamily IPv4 `
        -DestinationPrefix "192.168.10.0/24" -ErrorAction SilentlyContinue)
    if ($routes.Count -eq 0) {
        New-NetRoute -InterfaceIndex $index -AddressFamily IPv4 `
            -DestinationPrefix "192.168.10.0/24" -NextHop "0.0.0.0" `
            -RouteMetric 1 -PolicyStore ActiveStore -ErrorAction Stop | Out-Null
        $script:TemporaryRouteCreated = $true
    }
    Write-AppLog "Rete PACE temporanea pronta sull'indice $index."
}

function Restore-TemporaryPaceNetwork {
    if ($null -eq $script:TemporaryInterfaceIndex) { return }
    $index = $script:TemporaryInterfaceIndex
    try {
        Write-AppLog "Ripristino della scheda '$($script:TemporaryInterfaceAlias)'."
        if ($script:TemporaryRouteCreated) {
            Get-NetRoute -InterfaceIndex $index -AddressFamily IPv4 `
                -DestinationPrefix "192.168.10.0/24" -ErrorAction SilentlyContinue |
                Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
        }
        Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 `
            -IPAddress $script:TemporaryIpAddress -ErrorAction SilentlyContinue |
            Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue

        $currentDhcp = [string](Get-NetIPInterface -InterfaceIndex $index `
            -AddressFamily IPv4 -ErrorAction Stop).Dhcp
        if ($currentDhcp -ne $script:OriginalDhcpState) {
            Set-NetIPInterface -InterfaceIndex $index -AddressFamily IPv4 `
                -Dhcp $script:OriginalDhcpState -ErrorAction Stop
        }
        Write-AppLog "Scheda ripristinata (DHCP=$($script:OriginalDhcpState))."
    }
    catch {
        Write-AppLog "ATTENZIONE: ripristino rete incompleto: $($_.Exception.Message)"
    }
    finally {
        $script:TemporaryInterfaceIndex = $null
        $script:TemporaryRouteCreated = $false
    }
}

function Open-PaceSocket {
    param([string]$Address, [int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        if ($null -ne $script:TemporaryInterfaceIndex) {
            $endpoint = [System.Net.IPEndPoint]::new(
                [System.Net.IPAddress]::Parse($script:TemporaryIpAddress), 0)
            $client.Client.Bind($endpoint)
        }
        $pending = $client.BeginConnect($Address, $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(4000)) {
            throw "Timeout di connessione a ${Address}:$Port."
        }
        $client.EndConnect($pending)
        $client.NoDelay = $true
        $stream = $client.GetStream()
        $stream.ReadTimeout = 2500
        $stream.WriteTimeout = 2500
        $reader = New-Object System.IO.StreamReader($stream, [Text.Encoding]::ASCII, $false, 1024, $true)
        $writer = New-Object System.IO.StreamWriter($stream, [Text.Encoding]::ASCII, 1024, $true)
        $writer.NewLine = "`r`n"
        $writer.AutoFlush = $true
        return [pscustomobject]@{
            Client = $client
            Stream = $stream
            Reader = $reader
            Writer = $writer
        }
    }
    catch {
        try { $client.Close() } catch {}
        throw
    }
}

function Send-Scpi {
    param([string]$Command)
    if (-not $script:Connected -or $null -eq $script:Connection) {
        throw "PACE non connesso."
    }
    $script:Connection.Writer.WriteLine($Command)
    Write-AppLog "TX $Command"
}

function Query-Scpi {
    param([string]$Command, [int]$TimeoutMs = 2500, [switch]$Quiet)
    if (-not $script:Connected -or $null -eq $script:Connection) {
        throw "PACE non connesso."
    }
    $script:Connection.Stream.ReadTimeout = $TimeoutMs
    $script:Connection.Writer.WriteLine($Command)
    $reply = $script:Connection.Reader.ReadLine()
    if ($null -eq $reply) { throw "Connessione chiusa dal PACE." }
    if (-not $Quiet) { Write-AppLog "TX $Command | RX $reply" }
    return $reply.Trim()
}

function Get-ScpiPayload {
    param([AllowNull()][string]$Reply)
    if ([string]::IsNullOrWhiteSpace($Reply)) { return "" }
    $trimmed = $Reply.Trim()
    if ($trimmed -match '^\s*:[A-Za-z0-9:]+\s+(.+)$') {
        return $matches[1].Trim().Trim('"')
    }
    return $trimmed.Trim('"')
}

function Get-ScpiNumbers {
    param([AllowNull()][string]$Reply)
    $payload = Get-ScpiPayload $Reply
    $found = [regex]::Matches($payload, '[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?')
    $values = @()
    foreach ($match in $found) {
        $values += [double]::Parse($match.Value, $script:Culture)
    }
    return $values
}

function Get-ScpiNumber {
    param([AllowNull()][string]$Reply)
    $values = @(Get-ScpiNumbers $Reply)
    if ($values.Count -eq 0) { throw "Risposta numerica non valida: $Reply" }
    return [double]$values[$values.Count - 1]
}

function Get-PressureValueInBar {
    param([string]$Reply)
    $value = Get-ScpiNumber $Reply
    $payload = (Get-ScpiPayload $Reply).ToUpperInvariant()
    if ($payload -match 'MBAR') { return $value / 1000.0 }
    if ($payload -match 'HPA') { return $value / 1000.0 }
    if ($payload -match 'KPA') { return $value / 100.0 }
    if ($payload -match 'MPA') { return $value * 10.0 }
    if ($payload -match '(?<![KM])PA') { return $value / 100000.0 }
    if ($payload -match 'PSI') { return $value * 0.06894757293 }
    return $value
}

function Format-ScpiNumber {
    param([double]$Value)
    return $Value.ToString("0.########", $script:Culture)
}

function Assert-NoScpiError {
    $reply = Query-Scpi ":SYST:ERR?"
    $payload = Get-ScpiPayload $reply
    if ($payload -notmatch '^\s*0(?:\D|$)') {
        throw "Il PACE ha rifiutato un comando: $payload"
    }
}

function Close-PaceSocket {
    if ($null -eq $script:Connection) { return }
    try { $script:Connection.Writer.WriteLine(":LOC") } catch {}
    try { $script:Connection.Reader.Dispose() } catch {}
    try { $script:Connection.Writer.Dispose() } catch {}
    try { $script:Connection.Stream.Dispose() } catch {}
    try { $script:Connection.Client.Close() } catch {}
    $script:Connection = $null
}

function Get-SelectedModule {
    if ($null -eq $script:cmbModule -or $script:cmbModule.SelectedIndex -lt 0) { return 1 }
    return $script:cmbModule.SelectedIndex + 1
}

function Try-ParseUserDouble {
    param([string]$Text, [ref]$Result)
    $normalized = $Text.Trim().Replace(',', '.')
    $value = 0.0
    $ok = [double]::TryParse(
        $normalized,
        [Globalization.NumberStyles]::Float,
        $script:Culture,
        [ref]$value)
    if ($ok) { $Result.Value = $value }
    return $ok
}

function Assert-LeakSettingsValid {
    param(
        [double]$ReferenceDrop,
        [double]$GreenMinutes,
        [double]$YellowMinutes,
        [double]$OrangeMinutes
    )
    if ($ReferenceDrop -le 0) { throw "Il calo di riferimento deve essere maggiore di zero." }
    if ($OrangeMinutes -le 0) { throw "Il tempo arancione deve essere maggiore di zero." }
    if ($GreenMinutes -le $YellowMinutes -or $YellowMinutes -le $OrangeMinutes) {
        throw "I tempi devono rispettare: verde > giallo > arancione."
    }
}

function Load-LeakSettings {
    if (-not (Test-Path -LiteralPath $script:SettingsPath)) { return }
    try {
        $saved = Get-Content -LiteralPath $script:SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $reference = [double]$saved.LeakReferenceDropBar
        $green = [double]$saved.LeakGreenMinutes
        $yellow = [double]$saved.LeakYellowMinutes
        $orange = [double]$saved.LeakOrangeMinutes
        Assert-LeakSettingsValid $reference $green $yellow $orange
        $script:LeakReferenceDropBar = $reference
        $script:LeakGreenMinutes = $green
        $script:LeakYellowMinutes = $yellow
        $script:LeakOrangeMinutes = $orange
    }
    catch {
        Write-AppLog "Impostazioni perdite non valide; caricati i valori predefiniti. $($_.Exception.Message)"
    }
}

function Save-LeakSettings {
    $settings = [ordered]@{
        LeakReferenceDropBar = $script:LeakReferenceDropBar
        LeakGreenMinutes = $script:LeakGreenMinutes
        LeakYellowMinutes = $script:LeakYellowMinutes
        LeakOrangeMinutes = $script:LeakOrangeMinutes
    }
    $settings | ConvertTo-Json | Set-Content -LiteralPath $script:SettingsPath -Encoding UTF8
}

function Update-LeakSettingsPreview {
    if ($null -eq $script:lblLeakThresholdPreview) { return }
    $greenRate = $script:LeakReferenceDropBar / $script:LeakGreenMinutes
    $yellowRate = $script:LeakReferenceDropBar / $script:LeakYellowMinutes
    $orangeRate = $script:LeakReferenceDropBar / $script:LeakOrangeMinutes
    $script:lblLeakThresholdPreview.Text = (
        "Soglie equivalenti:`r`n" +
        "VERDE: fino a {0:0.000000} bar/min`r`n" +
        "GIALLO: oltre {0:0.000000} e fino a {1:0.000000} bar/min`r`n" +
        "ARANCIO: oltre {1:0.000000} e fino a {2:0.000000} bar/min`r`n" +
        "ROSSO: oltre {2:0.000000} bar/min" -f $greenRate, $yellowRate, $orangeRate)
}

function Set-LeakMonitorDisplay {
    param([string]$Side, [string]$Level, [double]$RateBarPerMinute = [double]::NaN, [double]$SpanMinutes = 0)

    if ($Side -eq "Sample") {
        $box = $script:grpSampleLeak
        $statusLabel = $script:lblSampleLeakStatus
        $detailLabel = $script:lblSampleLeakDetail
    }
    else {
        $box = $script:grpSourceLeak
        $statusLabel = $script:lblSourceLeakStatus
        $detailLabel = $script:lblSourceLeakDetail
    }
    if ($null -eq $statusLabel) { return }

    switch ($Level) {
        "GREEN" {
            $statusLabel.Text = "NO PERDITA"
            $statusLabel.ForeColor = [Drawing.Color]::ForestGreen
            $box.BackColor = [Drawing.Color]::Honeydew
        }
        "YELLOW" {
            $statusLabel.Text = "ATTENZIONE, lieve perdita"
            $statusLabel.ForeColor = [Drawing.Color]::DarkGoldenrod
            $box.BackColor = [Drawing.Color]::LightYellow
        }
        "ORANGE" {
            $statusLabel.Text = "ATTENZIONE, perdita pressione"
            $statusLabel.ForeColor = [Drawing.Color]::DarkOrange
            $box.BackColor = [Drawing.Color]::PeachPuff
        }
        "RED" {
            $statusLabel.Text = "ATTENZIONE, PERDITA SIGNIFICATIVA PRESSIONE"
            $statusLabel.ForeColor = [Drawing.Color]::Firebrick
            $box.BackColor = [Drawing.Color]::MistyRose
        }
        "PAUSED" {
            $statusLabel.Text = "VALUTAZIONE IN PAUSA (CONTROL)"
            $statusLabel.ForeColor = [Drawing.Color]::DimGray
            $box.BackColor = [Drawing.Color]::WhiteSmoke
        }
        "DISCONNECTED" {
            $statusLabel.Text = "IN ATTESA CONNESSIONE"
            $statusLabel.ForeColor = [Drawing.Color]::DimGray
            $box.BackColor = [Drawing.Color]::WhiteSmoke
        }
        default {
            $statusLabel.Text = "IN VALUTAZIONE"
            $statusLabel.ForeColor = [Drawing.Color]::SteelBlue
            $box.BackColor = [Drawing.Color]::AliceBlue
        }
    }

    if ([double]::IsNaN($RateBarPerMinute)) {
        $detailLabel.Text = if ($Level -eq "PAUSED") { "Il calcolo riparte automaticamente in MEASURE" } elseif ($Level -eq "DISCONNECTED") { "--" } else { "Raccolta dati..." }
    }
    else {
        $detailLabel.Text = "Calo stimato: $((10.0 * $RateBarPerMinute).ToString('0.000000', $script:Culture)) bar/10 min | dati: $($SpanMinutes.ToString('0.0', $script:Culture)) min"
    }
}

function Reset-LeakMonitoring {
    param([string]$DisplayState = "EVALUATING")
    $script:SampleLeakHistory = @()
    $script:SourceLeakHistory = @()
    Set-LeakMonitorDisplay -Side "Sample" -Level $DisplayState
    Set-LeakMonitorDisplay -Side "Source" -Level $DisplayState
}

function Add-LeakHistoryPoint {
    param([string]$Side, [datetime]$Time, [double]$Value)
    $point = [pscustomobject]@{ Time = $Time; Value = $Value }
    $keepMinutes = [Math]::Max($script:LeakGreenMinutes, [Math]::Max($script:LeakYellowMinutes, $script:LeakOrangeMinutes)) + 1.0
    $cutoff = $Time.AddMinutes(-$keepMinutes)
    if ($Side -eq "Sample") {
        $script:SampleLeakHistory += $point
        $script:SampleLeakHistory = @($script:SampleLeakHistory | Where-Object { $_.Time -ge $cutoff })
        return @($script:SampleLeakHistory)
    }
    $script:SourceLeakHistory += $point
    $script:SourceLeakHistory = @($script:SourceLeakHistory | Where-Object { $_.Time -ge $cutoff })
    return @($script:SourceLeakHistory)
}

function Get-LeakAssessment {
    param([array]$History)
    if ($History.Count -lt 2) {
        return [pscustomobject]@{ Level = "EVALUATING"; Rate = [double]::NaN; Span = 0.0 }
    }

    $baseTime = [datetime]$History[0].Time
    $lastTime = [datetime]$History[$History.Count - 1].Time
    $spanMinutes = ($lastTime - $baseTime).TotalMinutes
    if ($spanMinutes -le 0) {
        return [pscustomobject]@{ Level = "EVALUATING"; Rate = [double]::NaN; Span = 0.0 }
    }

    $observedDrop = [double]$History[0].Value - [double]$History[$History.Count - 1].Value
    if ($spanMinutes -lt $script:LeakOrangeMinutes -and $observedDrop -gt $script:LeakReferenceDropBar) {
        return [pscustomobject]@{ Level = "RED"; Rate = ($observedDrop / $spanMinutes); Span = $spanMinutes }
    }

    $sumX = 0.0
    $sumY = 0.0
    $sumXX = 0.0
    $sumXY = 0.0
    foreach ($point in $History) {
        $x = (([datetime]$point.Time) - $baseTime).TotalMinutes
        $y = [double]$point.Value
        $sumX += $x
        $sumY += $y
        $sumXX += $x * $x
        $sumXY += $x * $y
    }
    $count = [double]$History.Count
    $denominator = $count * $sumXX - $sumX * $sumX
    if ([Math]::Abs($denominator) -lt 1e-15) {
        return [pscustomobject]@{ Level = "EVALUATING"; Rate = [double]::NaN; Span = $spanMinutes }
    }
    $slope = ($count * $sumXY - $sumX * $sumY) / $denominator
    $lossRate = [Math]::Max(0.0, -$slope)
    $greenRate = $script:LeakReferenceDropBar / $script:LeakGreenMinutes
    $yellowRate = $script:LeakReferenceDropBar / $script:LeakYellowMinutes
    $orangeRate = $script:LeakReferenceDropBar / $script:LeakOrangeMinutes

    $minimumRedObservation = [Math]::Min(0.25, [Math]::Max(0.05, $script:LeakOrangeMinutes / 4.0))
    if ($spanMinutes -ge $minimumRedObservation -and $lossRate -gt $orangeRate) {
        return [pscustomobject]@{ Level = "RED"; Rate = $lossRate; Span = $spanMinutes }
    }
    if ($spanMinutes -lt $script:LeakOrangeMinutes) {
        return [pscustomobject]@{ Level = "EVALUATING"; Rate = $lossRate; Span = $spanMinutes }
    }
    if ($lossRate -gt $yellowRate) {
        return [pscustomobject]@{ Level = "ORANGE"; Rate = $lossRate; Span = $spanMinutes }
    }
    if ($spanMinutes -lt $script:LeakYellowMinutes) {
        return [pscustomobject]@{ Level = "EVALUATING"; Rate = $lossRate; Span = $spanMinutes }
    }
    if ($lossRate -gt $greenRate) {
        return [pscustomobject]@{ Level = "YELLOW"; Rate = $lossRate; Span = $spanMinutes }
    }
    if ($spanMinutes -lt $script:LeakGreenMinutes) {
        return [pscustomobject]@{ Level = "EVALUATING"; Rate = $lossRate; Span = $spanMinutes }
    }
    return [pscustomobject]@{ Level = "GREEN"; Rate = $lossRate; Span = $spanMinutes }
}

function Update-LeakMonitoring {
    param([datetime]$Time, [double]$SamplePressure, [double]$SourcePressure)
    if ($script:CurrentOutputOn -or $script:Automation.Active) {
        Reset-LeakMonitoring -DisplayState "PAUSED"
        return
    }
    $sampleHistory = @(Add-LeakHistoryPoint -Side "Sample" -Time $Time -Value $SamplePressure)
    $sourceHistory = @(Add-LeakHistoryPoint -Side "Source" -Time $Time -Value $SourcePressure)
    $sampleResult = Get-LeakAssessment -History $sampleHistory
    $sourceResult = Get-LeakAssessment -History $sourceHistory
    Set-LeakMonitorDisplay -Side "Sample" -Level $sampleResult.Level -RateBarPerMinute $sampleResult.Rate -SpanMinutes $sampleResult.Span
    Set-LeakMonitorDisplay -Side "Source" -Level $sourceResult.Level -RateBarPerMinute $sourceResult.Rate -SpanMinutes $sourceResult.Span
}

function Set-ConnectionUi {
    param([bool]$IsConnected, [string]$Text)
    $script:Connected = $IsConnected
    $script:btnConnect.Enabled = -not $IsConnected
    $script:btnDisconnect.Enabled = $IsConnected
    $script:tabs.Enabled = $IsConnected
    $script:lblConnection.Text = $Text
    $script:lblConnection.ForeColor = if ($IsConnected) { [Drawing.Color]::ForestGreen } else { [Drawing.Color]::Firebrick }
    if (-not $IsConnected) { Reset-LeakMonitoring -DisplayState "DISCONNECTED" }
}

function Set-AutomationUi {
    param([bool]$IsRunning)
    $script:cmbModule.Enabled = -not $IsRunning
    $script:btnApplyTarget.Enabled = -not $IsRunning
    $script:btnVent.Enabled = -not $IsRunning
    $script:btnReload.Enabled = -not $IsRunning
    $script:btnStartIndent.Enabled = -not $IsRunning
    $script:btnStartRoutine.Enabled = -not $IsRunning
    $script:gridRoutine.ReadOnly = $IsRunning
    $script:btnAddStep.Enabled = -not $IsRunning
    $script:btnRemoveStep.Enabled = -not $IsRunning
    $script:btnLoadRoutine.Enabled = -not $IsRunning
}

function Connect-PaceDevice {
    if ($script:Connected) { return }
    try {
        $script:btnConnect.Enabled = $false
        $script:lblConnection.Text = "Connessione..."
        [System.Windows.Forms.Application]::DoEvents()
        $address = $script:txtAddress.Text.Trim()
        $port = [int]$script:numPort.Value

        try {
            $script:Connection = Open-PaceSocket -Address $address -Port $port
        }
        catch {
            Write-AppLog "Prima connessione fallita: $($_.Exception.Message)"
            Enable-TemporaryPaceNetwork
            $script:Connection = Open-PaceSocket -Address $address -Port $port
        }

        $script:Connected = $true
        $idn = Query-Scpi "*IDN?"
        if ($idn -notmatch '(?i)PACE|Druck|GE Druck|GE Sensing') {
            throw "Il dispositivo risponde ma non si identifica come Druck PACE: $idn"
        }
        Send-Scpi "*CLS"
        $module = Get-SelectedModule
        Send-Scpi ":UNIT${module}:PRES BAR"
        Assert-NoScpiError
        $confirmedUnit = Get-ScpiPayload (Query-Scpi ":UNIT${module}:PRES?")
        if ($confirmedUnit -notmatch '(?i)^BAR$') {
            throw "Il PACE non ha confermato l'unita BAR (risposta: $confirmedUnit)."
        }

        Set-ConnectionUi $true "Connesso: $idn"
        $script:SupplyInterlockLatched = $false
        $script:pollTimer.Start()
        Load-DeviceSettings
        Write-AppLog "PACE connesso. Unita operative impostate su BAR."
    }
    catch {
        $message = $_.Exception.Message
        $script:Connected = $false
        Close-PaceSocket
        Restore-TemporaryPaceNetwork
        Set-ConnectionUi $false "Non connesso"
        Show-Error $message
    }
    finally {
        if (-not $script:Connected) { $script:btnConnect.Enabled = $true }
    }
}

function Disconnect-PaceDevice {
    param([bool]$RequestMeasure = $false)
    $script:pollTimer.Stop()
    $script:Automation.Active = $false
    $script:SupplyInterlockLatched = $false
    Set-AutomationUi $false
    if ($script:Connected -and $RequestMeasure) {
        try { Set-MeasureMode } catch {}
    }
    $script:Connected = $false
    Close-PaceSocket
    Restore-TemporaryPaceNetwork
    Set-ConnectionUi $false "Non connesso"
    Write-AppLog "PACE disconnesso."
}

function Load-DeviceSettings {
    if (-not $script:Connected) { return }
    $module = Get-SelectedModule
    try {
        Send-Scpi "*CLS"
        Send-Scpi ":UNIT${module}:PRES BAR"
        Assert-NoScpiError

        $target = Get-ScpiNumber (Query-Scpi ":SOUR${module}:PRES?")
        $slew = Get-ScpiNumber (Query-Scpi ":SOUR${module}:PRES:SLEW?")
        $slewMode = Get-ScpiPayload (Query-Scpi ":SOUR${module}:PRES:SLEW:MODE?")
        $outputMode = Get-ScpiPayload (Query-Scpi ":OUTP${module}:MODE?")
        $overshoot = Get-ScpiNumber (Query-Scpi ":SOUR${module}:PRES:SLEW:OVER?")
        $inLimit = Get-ScpiNumber (Query-Scpi ":SOUR${module}:PRES:INL?")
        $inLimitTime = Get-ScpiNumber (Query-Scpi ":SOUR${module}:PRES:INL:TIME?")
        $ventRate = Get-ScpiNumber (Query-Scpi ":SOUR${module}:PRES:LEV:IMM:AMPL:VENT:RATE?")
        $rangeReply = Query-Scpi ":SOUR${module}:PRES:RANG?"
        $rangeValues = @(Get-ScpiNumbers $rangeReply)
        if ($rangeValues.Count -gt 0) {
            $script:RangeMaximum = Get-PressureValueInBar $rangeReply
        }
        try {
            $script:RangeMinimum = Get-PressureValueInBar (Query-Scpi ":SOUR${module}:PRES:RANG:LOW?")
        }
        catch { $script:RangeMinimum = [double]::NaN }

        $script:txtTarget.Text = $target.ToString("0.######", $script:Culture)
        $script:txtSlew.Text = $slew.ToString("0.######", $script:Culture)
        $script:cmbSlewMode.SelectedIndex = if ($slewMode -match '(?i)MAX') { 1 } else { 0 }
        if ($outputMode -match '(?i)PASS') { $script:cmbControlMode.SelectedIndex = 1 }
        elseif ($outputMode -match '(?i)GAUG') { $script:cmbControlMode.SelectedIndex = 2 }
        else { $script:cmbControlMode.SelectedIndex = 0 }
        $script:chkOvershoot.Checked = ($overshoot -ne 0)
        $script:txtInLimit.Text = $inLimit.ToString("0.######", $script:Culture)
        $script:numInLimitTime.Value = [decimal][Math]::Max(1, [Math]::Min(60, $inLimitTime))
        $script:txtVentRate.Text = $ventRate.ToString("0.######", $script:Culture)
        $minText = if ([double]::IsNaN($script:RangeMinimum)) { "?" } else { $script:RangeMinimum.ToString("0.###", $script:Culture) }
        $maxText = if ([double]::IsNaN($script:RangeMaximum)) { "?" } else { $script:RangeMaximum.ToString("0.###", $script:Culture) }
        $script:lblRange.Text = "Range: $minText ... $maxText bar"
        Write-AppLog "Modulo $module caricato. Range $minText ... $maxText bar."
    }
    catch {
        Show-Error "Impossibile leggere le impostazioni del modulo ${module}: $($_.Exception.Message)"
    }
}

function Set-MeasureMode {
    if (-not $script:Connected) { return }
    $module = Get-SelectedModule
    Send-Scpi ":OUTP${module}:STAT OFF"
    Assert-NoScpiError
    $script:CurrentOutputOn = $false
    $script:lblModeValue.Text = "MEASURE"
    $script:lblModeValue.ForeColor = [Drawing.Color]::RoyalBlue
    Reset-LeakMonitoring -DisplayState "EVALUATING"
    Write-AppLog "Modulo $module impostato su MEASURE."
}

function Set-ControlMode {
    if (-not $script:Connected) { return }
    $module = Get-SelectedModule
    Send-Scpi ":OUTP${module}:STAT ON"
    Assert-NoScpiError
    $script:CurrentOutputOn = $true
    Reset-LeakMonitoring -DisplayState "PAUSED"
    Write-AppLog "Modulo $module impostato su CONTROL."
}

function Invoke-SupplyMarginInterlock {
    param([double]$SourcePressure, [double]$BenchPressure)

    $margin = $SourcePressure - $BenchPressure
    $script:lblSupplyMarginValue.Text = $margin.ToString("0.000", $script:Culture) + " bar"
    if ($margin -lt $script:MinimumSupplyMarginBar) {
        $script:lblSupplyMarginValue.ForeColor = [Drawing.Color]::Firebrick
    }
    elseif ($margin -lt 3.0) {
        $script:lblSupplyMarginValue.ForeColor = [Drawing.Color]::DarkOrange
    }
    else {
        $script:lblSupplyMarginValue.ForeColor = [Drawing.Color]::ForestGreen
    }

    if ($script:CurrentOutputOn -and $margin -lt $script:MinimumSupplyMarginBar) {
        if (-not $script:SupplyInterlockLatched) {
            $script:SupplyInterlockLatched = $true
            $script:Automation.Active = $false
            $script:Automation.State = "Idle"
            Set-AutomationUi $false

            try {
                Set-MeasureMode
                $script:lblAutomation.Text = "INTERLOCK SORGENTE: MEASURE"
                Write-AppLog ("INTERLOCK SORGENTE: CONTROL interrotto. Sorgente={0:0.000} bar, pressione={1:0.000} bar, margine={2:0.000} bar." -f $SourcePressure, $BenchPressure, $margin)
                [System.Windows.Forms.MessageBox]::Show(
                    ("Il PACE e stato portato automaticamente in MEASURE.`r`n`r`nPressione sorgente: {0:0.000} bar`r`nPressione attuale: {1:0.000} bar`r`nMargine: {2:0.000} bar`r`n`r`nIl margine minimo richiesto e {3:0.0} bar. La routine in corso e stata annullata." -f $SourcePressure, $BenchPressure, $margin, $script:MinimumSupplyMarginBar),
                    "Protezione pressione sorgente",
                    [System.Windows.Forms.MessageBoxButtons]::OK,
                    [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
            }
            catch {
                $script:SupplyInterlockLatched = $false
                Write-AppLog "ERRORE CRITICO INTERLOCK: impossibile confermare MEASURE: $($_.Exception.Message)"
                [System.Windows.Forms.MessageBox]::Show(
                    "Margine della sorgente inferiore a 2 bar, ma il comando MEASURE non e stato confermato. Porta immediatamente il PACE in MEASURE dal pannello frontale.",
                    "INTERLOCK - intervento manuale richiesto",
                    [System.Windows.Forms.MessageBoxButtons]::OK,
                    [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
                throw "Interlock sorgente: comando MEASURE non confermato."
            }
        }
    }
    elseif ($margin -ge $script:SupplyInterlockResetMarginBar) {
        $script:SupplyInterlockLatched = $false
    }
}

function Validate-TargetRange {
    param([double]$Target)
    if (-not [double]::IsNaN($script:RangeMinimum) -and $Target -lt $script:RangeMinimum) {
        throw "Target $Target bar inferiore al limite del modulo ($($script:RangeMinimum) bar)."
    }
    if (-not [double]::IsNaN($script:RangeMaximum) -and $Target -gt $script:RangeMaximum) {
        throw "Target $Target bar superiore al limite del modulo ($($script:RangeMaximum) bar)."
    }
}

function Confirm-SafetyWarnings {
    param([array]$Steps, [double]$StartingPressure)
    $highSlew = $false
    $largeIncrease = $false
    $previous = $StartingPressure
    foreach ($step in $Steps) {
        Validate-TargetRange ([double]$step.Target)
        if (-not [double]::IsNaN($script:CurrentSourcePositive)) {
            $plannedMargin = $script:CurrentSourcePositive - [double]$step.Target
            if ($plannedMargin -lt $script:MinimumSupplyMarginBar) {
                throw ("Target {0:0.######} bar bloccato: con la sorgente a {1:0.######} bar lascerebbe un margine di soli {2:0.######} bar. Il minimo richiesto e {3:0.0} bar." -f ([double]$step.Target), $script:CurrentSourcePositive, $plannedMargin, $script:MinimumSupplyMarginBar)
            }
        }
        if ($step.UseMaximumRate -or [double]$step.Slew -gt 0.5) { $highSlew = $true }
        if (([double]$step.Target - $previous) -ge 10.0) { $largeIncrease = $true }
        $previous = [double]$step.Target
    }

    $warnings = @()
    if ($largeIncrease) {
        $warnings += "Almeno un incremento di pressione e pari o superiore a 10 bar."
    }
    if ($highSlew) {
        $warnings += "La slew rate e superiore a 0.5 bar/s oppure e impostata su MAXIMUM."
    }
    if ($warnings.Count -eq 0) { return $true }

    $text = ($warnings -join "`r`n") + "`r`n`r`nVerifica collegamenti, limiti e campione. Applicare comunque?"
    $answer = [System.Windows.Forms.MessageBox]::Show(
        $text,
        "Conferma di sicurezza",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Warning,
        [System.Windows.Forms.MessageBoxDefaultButton]::Button2)
    return ($answer -eq [System.Windows.Forms.DialogResult]::Yes)
}

function Get-CommonPressureSettings {
    $inLimit = 0.0
    $ventRate = 0.0
    if (-not (Try-ParseUserDouble $script:txtInLimit.Text ([ref]$inLimit)) -or $inLimit -lt 0.0001 -or $inLimit -gt 10) {
        throw "La tolleranza In-limits deve essere tra 0.0001 e 10 %FS."
    }
    if (-not (Try-ParseUserDouble $script:txtVentRate.Text ([ref]$ventRate)) -or $ventRate -le 0) {
        throw "La velocita di vent deve essere maggiore di zero."
    }
    return [pscustomobject]@{
        InLimit = $inLimit
        InLimitTime = [int]$script:numInLimitTime.Value
        VentRate = $ventRate
        Overshoot = $script:chkOvershoot.Checked
        ControlMode = @("ACT", "PASS", "GAUG")[$script:cmbControlMode.SelectedIndex]
    }
}

function Apply-PressureStep {
    param([pscustomobject]$Step)
    $module = Get-SelectedModule
    $settings = Get-CommonPressureSettings
    Reset-LeakMonitoring -DisplayState "PAUSED"
    Send-Scpi "*CLS"
    Send-Scpi ":UNIT${module}:PRES BAR"
    Send-Scpi ":OUTP${module}:MODE $($settings.ControlMode)"
    Send-Scpi ":SOUR${module}:PRES:SLEW:OVER $([int]$settings.Overshoot)"
    Send-Scpi ":SOUR${module}:PRES:INL $(Format-ScpiNumber $settings.InLimit)"
    Send-Scpi ":SOUR${module}:PRES:INL:TIME $($settings.InLimitTime)"
    if ($Step.UseMaximumRate) {
        Send-Scpi ":SOUR${module}:PRES:SLEW:MODE MAX"
    }
    else {
        Send-Scpi ":SOUR${module}:PRES:SLEW:MODE LIN"
        Send-Scpi ":SOUR${module}:PRES:SLEW $(Format-ScpiNumber ([double]$Step.Slew))"
    }
    Send-Scpi ":SOUR${module}:PRES $(Format-ScpiNumber ([double]$Step.Target))"
    Send-Scpi ":OUTP${module}:STAT ON"
    Assert-NoScpiError
    Write-AppLog "Step: target=$($Step.Target) bar, slew=$($Step.Slew) bar/s, dwell=$($Step.Dwell) s."
}

function Start-AutomationSequence {
    param([string]$Mode, [array]$Steps, [bool]$KeepControlAtEnd)
    if ($Steps.Count -eq 0) { throw "La routine non contiene step." }
    if ([double]::IsNaN($script:CurrentPressure)) { Poll-PaceData }
    if ([double]::IsNaN($script:CurrentSourcePositive)) {
        throw "Pressione della sorgente non disponibile: per sicurezza non e possibile avviare CONTROL."
    }
    $currentSupplyMargin = $script:CurrentSourcePositive - $script:CurrentPressure
    if ($currentSupplyMargin -lt $script:MinimumSupplyMarginBar) {
        throw ("Avvio bloccato: il margine attuale tra sorgente e pressione sul banchino e {0:0.######} bar. Servono almeno {1:0.0} bar." -f $currentSupplyMargin, $script:MinimumSupplyMarginBar)
    }
    if ($script:SupplyInterlockLatched -and $currentSupplyMargin -lt $script:SupplyInterlockResetMarginBar) {
        throw ("Protezione sorgente ancora attiva: dopo un intervento il margine deve risalire ad almeno {0:0.0} bar prima di riabilitare CONTROL." -f $script:SupplyInterlockResetMarginBar)
    }
    if ($currentSupplyMargin -ge $script:SupplyInterlockResetMarginBar) {
        $script:SupplyInterlockLatched = $false
    }
    if (-not (Confirm-SafetyWarnings -Steps $Steps -StartingPressure $script:CurrentPressure)) {
        Write-AppLog "Operazione annullata dall'utente dopo l'avviso di sicurezza."
        return
    }

    $script:Automation.Active = $true
    $script:Automation.Mode = $Mode
    $script:Automation.State = "Starting"
    $script:Automation.Steps = $Steps
    $script:Automation.Index = 0
    $script:Automation.KeepControlAtEnd = $KeepControlAtEnd
    Set-AutomationUi $true
    try {
        Start-CurrentAutomationStep
    }
    catch {
        $script:Automation.Active = $false
        $script:Automation.State = "Idle"
        try { Set-MeasureMode } catch {}
        Set-AutomationUi $false
        throw
    }
}

function Start-CurrentAutomationStep {
    $step = $script:Automation.Steps[$script:Automation.Index]
    Apply-PressureStep $step
    $script:Automation.State = "WaitingTarget"
    $distance = if ([double]::IsNaN($script:CurrentPressure)) { 0 } else { [Math]::Abs([double]$step.Target - $script:CurrentPressure) }
    $rate = if ($step.UseMaximumRate) { 0.5 } else { [Math]::Max([double]$step.Slew, 0.001) }
    $timeoutSeconds = [Math]::Max(180, ($distance / $rate) * 3 + 120)
    $script:Automation.WaitDeadline = (Get-Date).AddSeconds($timeoutSeconds)
    $script:lblAutomation.Text = "$($script:Automation.Mode): step $($script:Automation.Index + 1)/$($script:Automation.Steps.Count), verso $($step.Target) bar"
}

function Complete-CurrentAutomationStep {
    $script:Automation.Index++
    if ($script:Automation.Index -lt $script:Automation.Steps.Count) {
        Start-CurrentAutomationStep
        return
    }

    $mode = $script:Automation.Mode
    $keep = $script:Automation.KeepControlAtEnd
    $script:Automation.Active = $false
    $script:Automation.State = "Idle"
    if (-not $keep) { Set-MeasureMode }
    Set-AutomationUi $false
    $script:lblAutomation.Text = "$mode completata" + $(if ($keep) { " - CONTROL mantenuto" } else { " - MEASURE" })
    Write-AppLog "$mode completata."
}

function Process-Automation {
    if (-not $script:Automation.Active) { return }
    try {
        if ($script:Automation.State -eq "WaitingTarget") {
            if ($script:CurrentInLimit) {
                $step = $script:Automation.Steps[$script:Automation.Index]
                if ([double]$step.Dwell -gt 0) {
                    $script:Automation.State = "Dwelling"
                    $script:Automation.DwellEnd = (Get-Date).AddSeconds([double]$step.Dwell)
                    $script:lblAutomation.Text = "$($script:Automation.Mode): permanenza $($step.Dwell) s al target"
                    Write-AppLog "Target raggiunto. Inizio permanenza di $($step.Dwell) s."
                }
                else { Complete-CurrentAutomationStep }
            }
            elseif ((Get-Date) -gt $script:Automation.WaitDeadline) {
                throw "Timeout: il PACE non ha raggiunto il target entro il tempo di sicurezza."
            }
        }
        elseif ($script:Automation.State -eq "Dwelling") {
            $remaining = [Math]::Max(0, [int](($script:Automation.DwellEnd - (Get-Date)).TotalSeconds))
            $script:lblAutomation.Text = "$($script:Automation.Mode): permanenza, $remaining s rimanenti"
            if ((Get-Date) -ge $script:Automation.DwellEnd) { Complete-CurrentAutomationStep }
        }
    }
    catch {
        $script:Automation.Active = $false
        try { Set-MeasureMode } catch {}
        Set-AutomationUi $false
        Show-Error "Automazione interrotta: $($_.Exception.Message) Il PACE e stato portato in MEASURE."
    }
}

function Stop-Automation {
    param([bool]$SetMeasure = $true)
    $wasActive = $script:Automation.Active
    $script:Automation.Active = $false
    $script:Automation.State = "Idle"
    if ($SetMeasure -and $script:Connected) { Set-MeasureMode }
    Set-AutomationUi $false
    $script:lblAutomation.Text = if ($wasActive) { "Routine interrotta - MEASURE" } else { "MEASURE" }
}

function Poll-PaceData {
    if (-not $script:Connected) { return }
    $module = Get-SelectedModule
    try {
        $pressure = Get-ScpiNumber (Query-Scpi ":SENS${module}:PRES:CONT?" -Quiet)
        $target = Get-ScpiNumber (Query-Scpi ":SOUR${module}:PRES?" -Quiet)
        $output = Get-ScpiNumber (Query-Scpi ":OUTP${module}:STAT?" -Quiet)
        $script:CurrentOutputOn = ($output -ne 0)
        $sourcePositive = Get-ScpiNumber (Query-Scpi ":SOUR${module}:PRES:COMP1?" -Quiet)
        $inLimitValues = @(Get-ScpiNumbers (Query-Scpi ":SENS${module}:PRES:INL?" -Quiet))
        $inLimit = ($inLimitValues.Count -gt 0 -and $inLimitValues[$inLimitValues.Count - 1] -ne 0)

        $sourceNegative = [double]::NaN
        $actualSlew = [double]::NaN
        $effort = [double]::NaN
        try { $sourceNegative = Get-ScpiNumber (Query-Scpi ":SOUR${module}:PRES:COMP2?" -Quiet) } catch {}
        try { $actualSlew = Get-ScpiNumber (Query-Scpi ":SENS${module}:PRES:SLEW?" -Quiet) } catch {}
        try { $effort = Get-ScpiNumber (Query-Scpi ":SOUR${module}:PRES:EFF?" -Quiet) } catch {}

        $script:CurrentPressure = $pressure
        $script:CurrentTarget = $target
        $script:CurrentSourcePositive = $sourcePositive
        $script:CurrentOutputOn = ($output -ne 0)
        $script:CurrentInLimit = $inLimit
        $script:PollFailures = 0

        $script:lblPressureValue.Text = $pressure.ToString("0.000000", $script:Culture) + " bar"
        $script:lblTargetValue.Text = $target.ToString("0.000000", $script:Culture) + " bar"
        $script:lblSourcePlusValue.Text = if ([double]::IsNaN($sourcePositive)) { "n/d" } else { $sourcePositive.ToString("0.000", $script:Culture) + " bar" }
        $script:lblSourceMinusValue.Text = if ([double]::IsNaN($sourceNegative)) { "n/d" } else { $sourceNegative.ToString("0.000", $script:Culture) + " bar" }
        $script:lblSlewValue.Text = if ([double]::IsNaN($actualSlew)) { "n/d" } else { $actualSlew.ToString("0.0000", $script:Culture) + " bar/s" }
        $script:lblEffortValue.Text = if ([double]::IsNaN($effort)) { "n/d" } else { $effort.ToString("0.0", $script:Culture) + " %" }
        $script:lblModeValue.Text = if ($script:CurrentOutputOn) { "CONTROL" } else { "MEASURE" }
        $script:lblModeValue.ForeColor = if ($script:CurrentOutputOn) { [Drawing.Color]::Firebrick } else { [Drawing.Color]::RoyalBlue }
        $script:lblLimitValue.Text = if ($inLimit) { "IN LIMIT" } else { "IN MOVIMENTO" }
        $script:lblLimitValue.ForeColor = if ($inLimit) { [Drawing.Color]::ForestGreen } else { [Drawing.Color]::DarkOrange }

        Invoke-SupplyMarginInterlock -SourcePressure $sourcePositive -BenchPressure $pressure

        $time = Get-Date
        Update-LeakMonitoring -Time $time -SamplePressure $pressure -SourcePressure $sourcePositive
        if (-not (Test-Path -LiteralPath $script:CsvPath)) {
            "Timestamp,Pressure_bar,Target_bar,Source_positive_bar,Source_negative_bar,Actual_slew_bar_s,Effort_percent,Control,In_limit" |
                Set-Content -LiteralPath $script:CsvPath -Encoding UTF8
        }
        $csvLine = @(
            $time.ToString("o"),
            $pressure.ToString("R", $script:Culture),
            $target.ToString("R", $script:Culture),
            $(if ([double]::IsNaN($sourcePositive)) { "" } else { $sourcePositive.ToString("R", $script:Culture) }),
            $(if ([double]::IsNaN($sourceNegative)) { "" } else { $sourceNegative.ToString("R", $script:Culture) }),
            $(if ([double]::IsNaN($actualSlew)) { "" } else { $actualSlew.ToString("R", $script:Culture) }),
            $(if ([double]::IsNaN($effort)) { "" } else { $effort.ToString("R", $script:Culture) }),
            [int]$script:CurrentOutputOn,
            [int]$inLimit
        ) -join ','
        Add-Content -LiteralPath $script:CsvPath -Value $csvLine -Encoding UTF8

        Process-Automation
    }
    catch {
        $script:PollFailures++
        Write-AppLog "Errore lettura $($script:PollFailures): $($_.Exception.Message)"
        if ($script:CurrentOutputOn) {
            $script:Automation.Active = $false
            $script:Automation.State = "Idle"
            Set-AutomationUi $false
            try {
                Set-MeasureMode
                $script:lblAutomation.Text = "Telemetria sorgente non disponibile - MEASURE"
                Write-AppLog "Fail-safe: lettura incompleta durante CONTROL, richiesto MEASURE."
            }
            catch {
                Write-AppLog "ERRORE CRITICO FAIL-SAFE: impossibile confermare MEASURE: $($_.Exception.Message)"
            }
        }
        if ($script:PollFailures -ge 3) {
            $script:pollTimer.Stop()
            $script:Automation.Active = $false
            Set-AutomationUi $false
            $script:Connected = $false
            Close-PaceSocket
            Restore-TemporaryPaceNetwork
            Set-ConnectionUi $false "Connessione persa"
            Show-Error "Connessione con il PACE persa. Il comando MEASURE non puo essere garantito: controlla immediatamente il pannello del PACE, portalo in MEASURE se necessario, poi verifica il cavo e riconnetti."
        }
    }
}

function New-PressureStep {
    param([double]$Target, [double]$Slew, [double]$Dwell, [bool]$UseMaximumRate = $false)
    return [pscustomobject]@{
        Target = $Target
        Slew = $Slew
        Dwell = $Dwell
        UseMaximumRate = $UseMaximumRate
    }
}

function Get-ManualStep {
    $target = 0.0
    $slew = 0.0
    if (-not (Try-ParseUserDouble $script:txtTarget.Text ([ref]$target))) { throw "Pressione target non valida." }
    if (-not (Try-ParseUserDouble $script:txtSlew.Text ([ref]$slew)) -or $slew -le 0) { throw "Slew rate non valida." }
    return New-PressureStep -Target $target -Slew $slew -Dwell 0 `
        -UseMaximumRate ($script:cmbSlewMode.SelectedIndex -eq 1)
}

function Get-RoutineStepsFromGrid {
    $steps = @()
    foreach ($row in $script:gridRoutine.Rows) {
        if ($row.IsNewRow) { continue }
        $target = 0.0
        $slew = 0.0
        $dwell = 0.0
        if (-not (Try-ParseUserDouble ([string]$row.Cells[0].Value) ([ref]$target))) {
            throw "Target non valido alla riga $($row.Index + 1)."
        }
        if (-not (Try-ParseUserDouble ([string]$row.Cells[1].Value) ([ref]$slew)) -or $slew -le 0) {
            throw "Slew rate non valida alla riga $($row.Index + 1)."
        }
        $dwellText = [string]$row.Cells[2].Value
        if ([string]::IsNullOrWhiteSpace($dwellText)) { $dwell = 0 }
        elseif (-not (Try-ParseUserDouble $dwellText ([ref]$dwell)) -or $dwell -lt 0) {
            throw "Permanenza non valida alla riga $($row.Index + 1)."
        }
        $steps += New-PressureStep -Target $target -Slew $slew -Dwell $dwell
    }
    return $steps
}

function Save-Routine {
    try {
        $steps = @(Get-RoutineStepsFromGrid)
        if ($steps.Count -eq 0) { throw "La routine e vuota." }
        $dialog = New-Object System.Windows.Forms.SaveFileDialog
        $dialog.Filter = "Routine PACE (*.json)|*.json"
        $dialog.DefaultExt = "json"
        $dialog.FileName = "routine_pace.json"
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $steps | Select-Object Target, Slew, Dwell | ConvertTo-Json |
                Set-Content -LiteralPath $dialog.FileName -Encoding UTF8
            Write-AppLog "Routine salvata: $($dialog.FileName)"
        }
    }
    catch { Show-Error $_.Exception.Message }
}

function Load-Routine {
    try {
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Filter = "Routine PACE (*.json)|*.json"
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $items = @(Get-Content -LiteralPath $dialog.FileName -Raw -Encoding UTF8 | ConvertFrom-Json)
            $script:gridRoutine.Rows.Clear()
            foreach ($item in $items) {
                [void]$script:gridRoutine.Rows.Add($item.Target, $item.Slew, $item.Dwell)
            }
            Write-AppLog "Routine caricata: $($dialog.FileName)"
        }
    }
    catch { Show-Error "Routine non valida: $($_.Exception.Message)" }
}

function Make-Label {
    param([string]$Text, [int]$X, [int]$Y, [int]$Width = 140, [int]$Height = 24)
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.Location = New-Object Drawing.Point($X, $Y)
    $label.Size = New-Object Drawing.Size($Width, $Height)
    $label.TextAlign = [Drawing.ContentAlignment]::MiddleLeft
    return $label
}

function Make-Button {
    param([string]$Text, [int]$X, [int]$Y, [int]$Width = 120, [int]$Height = 34)
    $button = New-Object System.Windows.Forms.Button
    $button.Text = $Text
    $button.Location = New-Object Drawing.Point($X, $Y)
    $button.Size = New-Object Drawing.Size($Width, $Height)
    $button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    return $button
}

function Add-MetricCard {
    param([System.Windows.Forms.Control]$Parent, [string]$Title, [int]$Width = 150)
    $panel = New-Object System.Windows.Forms.Panel
    $panel.Width = $Width
    $panel.Height = 76
    $panel.Margin = New-Object System.Windows.Forms.Padding(5)
    $panel.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $titleLabel = Make-Label $Title 8 6 ($Width - 16) 20
    $titleLabel.ForeColor = [Drawing.Color]::DimGray
    $valueLabel = Make-Label "--" 8 29 ($Width - 16) 34
    $valueLabel.Font = New-Object Drawing.Font("Segoe UI", 12, [Drawing.FontStyle]::Bold)
    $panel.Controls.Add($titleLabel)
    $panel.Controls.Add($valueLabel)
    $Parent.Controls.Add($panel)
    return $valueLabel
}

Load-LeakSettings

# ------------------------------- GUI ---------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = "PACE Pressure Controller $($script:Version)"
$form.Size = New-Object Drawing.Size(1280, 860)
$form.MinimumSize = New-Object Drawing.Size(1080, 720)
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.Font = New-Object Drawing.Font("Segoe UI", 9)
$form.BackColor = [Drawing.Color]::WhiteSmoke

$connectionPanel = New-Object System.Windows.Forms.Panel
$connectionPanel.Dock = [System.Windows.Forms.DockStyle]::Top
$connectionPanel.Height = 72
$connectionPanel.Padding = New-Object System.Windows.Forms.Padding(10)
$connectionPanel.BackColor = [Drawing.Color]::White

$connectionPanel.Controls.Add((Make-Label "PACE IP" 12 10 60 22))
$script:txtAddress = New-Object System.Windows.Forms.TextBox
$script:txtAddress.Text = $PaceAddress
$script:txtAddress.Location = New-Object Drawing.Point(75, 10)
$script:txtAddress.Size = New-Object Drawing.Size(125, 24)
$connectionPanel.Controls.Add($script:txtAddress)

$connectionPanel.Controls.Add((Make-Label "Porta" 215 10 45 22))
$script:numPort = New-Object System.Windows.Forms.NumericUpDown
$script:numPort.Minimum = 1
$script:numPort.Maximum = 65535
$script:numPort.Value = $TcpPort
$script:numPort.Location = New-Object Drawing.Point(263, 10)
$script:numPort.Size = New-Object Drawing.Size(75, 24)
$connectionPanel.Controls.Add($script:numPort)

$connectionPanel.Controls.Add((Make-Label "Modulo" 352 10 55 22))
$script:cmbModule = New-Object System.Windows.Forms.ComboBox
$script:cmbModule.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
[void]$script:cmbModule.Items.AddRange(@("1", "2"))
$script:cmbModule.SelectedIndex = 0
$script:cmbModule.Location = New-Object Drawing.Point(410, 10)
$script:cmbModule.Size = New-Object Drawing.Size(55, 24)
$connectionPanel.Controls.Add($script:cmbModule)

$script:btnConnect = Make-Button "Connetti" 480 7 100 31
$script:btnConnect.BackColor = [Drawing.Color]::Honeydew
$connectionPanel.Controls.Add($script:btnConnect)
$script:btnDisconnect = Make-Button "Disconnetti" 590 7 105 31
$script:btnDisconnect.Enabled = $false
$connectionPanel.Controls.Add($script:btnDisconnect)
$script:lblConnection = Make-Label "Non connesso" 710 7 520 31
$script:lblConnection.Font = New-Object Drawing.Font("Segoe UI", 9, [Drawing.FontStyle]::Bold)
$script:lblConnection.ForeColor = [Drawing.Color]::Firebrick
$connectionPanel.Controls.Add($script:lblConnection)
$script:lblRange = Make-Label "Range: --" 12 41 450 22
$connectionPanel.Controls.Add($script:lblRange)
$warningLabel = Make-Label "Comandi in bar e bar/s | Margine sorgente minimo: 2.0 bar" 480 41 650 22
$warningLabel.ForeColor = [Drawing.Color]::DarkRed
$connectionPanel.Controls.Add($warningLabel)

$metricsFlow = New-Object System.Windows.Forms.FlowLayoutPanel
$metricsFlow.Dock = [System.Windows.Forms.DockStyle]::Top
$metricsFlow.Height = 96
$metricsFlow.Padding = New-Object System.Windows.Forms.Padding(8)
$metricsFlow.WrapContents = $false
$metricsFlow.AutoScroll = $true
$metricsFlow.BackColor = [Drawing.Color]::Gainsboro
$script:lblPressureValue = Add-MetricCard $metricsFlow "Pressione attuale" 165
$script:lblTargetValue = Add-MetricCard $metricsFlow "Pressione target" 165
$script:lblSourcePlusValue = Add-MetricCard $metricsFlow "Sorgente positiva" 155
$script:lblSourceMinusValue = Add-MetricCard $metricsFlow "Sorgente negativa" 155
$script:lblSlewValue = Add-MetricCard $metricsFlow "Slew misurata" 155
$script:lblEffortValue = Add-MetricCard $metricsFlow "Sforzo valvole" 145
$script:lblModeValue = Add-MetricCard $metricsFlow "Stato" 120
$script:lblLimitValue = Add-MetricCard $metricsFlow "Target" 135
$script:lblSupplyMarginValue = Add-MetricCard $metricsFlow "Margine sorgente" 155

$leakPanel = New-Object System.Windows.Forms.Panel
$leakPanel.Dock = [System.Windows.Forms.DockStyle]::Top
$leakPanel.Height = 122
$leakPanel.Padding = New-Object System.Windows.Forms.Padding(8, 4, 8, 6)
$leakPanel.BackColor = [Drawing.Color]::White
$leakTitle = Make-Label "MONITORAGGIO PERDITE - valutazione automatica in MEASURE" 0 0 600 24
$leakTitle.Dock = [System.Windows.Forms.DockStyle]::Top
$leakTitle.Font = New-Object Drawing.Font("Segoe UI", 9, [Drawing.FontStyle]::Bold)
$leakTitle.TextAlign = [Drawing.ContentAlignment]::MiddleCenter
$leakPanel.Controls.Add($leakTitle)

$leakLayout = New-Object System.Windows.Forms.TableLayoutPanel
$leakLayout.Dock = [System.Windows.Forms.DockStyle]::Fill
$leakLayout.ColumnCount = 2
$leakLayout.RowCount = 1
$leakLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle -ArgumentList @([System.Windows.Forms.SizeType]::Percent, 50.0))) | Out-Null
$leakLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle -ArgumentList @([System.Windows.Forms.SizeType]::Percent, 50.0))) | Out-Null
$leakLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle -ArgumentList @([System.Windows.Forms.SizeType]::Percent, 100.0))) | Out-Null
$leakPanel.Controls.Add($leakLayout)
$leakTitle.BringToFront()

$script:grpSampleLeak = New-Object System.Windows.Forms.GroupBox
$script:grpSampleLeak.Text = "TENUTA LATO CAMPIONE"
$script:grpSampleLeak.Dock = [System.Windows.Forms.DockStyle]::Fill
$script:grpSampleLeak.Margin = New-Object System.Windows.Forms.Padding(6)
$script:lblSampleLeakStatus = Make-Label "IN ATTESA CONNESSIONE" 0 0 400 42
$script:lblSampleLeakStatus.Dock = [System.Windows.Forms.DockStyle]::Fill
$script:lblSampleLeakStatus.TextAlign = [Drawing.ContentAlignment]::MiddleCenter
$script:lblSampleLeakStatus.Font = New-Object Drawing.Font("Segoe UI", 12, [Drawing.FontStyle]::Bold)
$script:lblSampleLeakDetail = Make-Label "--" 0 0 400 24
$script:lblSampleLeakDetail.Dock = [System.Windows.Forms.DockStyle]::Bottom
$script:lblSampleLeakDetail.TextAlign = [Drawing.ContentAlignment]::MiddleCenter
$script:lblSampleLeakDetail.ForeColor = [Drawing.Color]::DimGray
$script:grpSampleLeak.Controls.Add($script:lblSampleLeakStatus)
$script:grpSampleLeak.Controls.Add($script:lblSampleLeakDetail)
$script:lblSampleLeakDetail.BringToFront()
$leakLayout.Controls.Add($script:grpSampleLeak, 0, 0)

$script:grpSourceLeak = New-Object System.Windows.Forms.GroupBox
$script:grpSourceLeak.Text = "TENUTA INLET / SORGENTE POSITIVA"
$script:grpSourceLeak.Dock = [System.Windows.Forms.DockStyle]::Fill
$script:grpSourceLeak.Margin = New-Object System.Windows.Forms.Padding(6)
$script:lblSourceLeakStatus = Make-Label "IN ATTESA CONNESSIONE" 0 0 400 42
$script:lblSourceLeakStatus.Dock = [System.Windows.Forms.DockStyle]::Fill
$script:lblSourceLeakStatus.TextAlign = [Drawing.ContentAlignment]::MiddleCenter
$script:lblSourceLeakStatus.Font = New-Object Drawing.Font("Segoe UI", 12, [Drawing.FontStyle]::Bold)
$script:lblSourceLeakDetail = Make-Label "--" 0 0 400 24
$script:lblSourceLeakDetail.Dock = [System.Windows.Forms.DockStyle]::Bottom
$script:lblSourceLeakDetail.TextAlign = [Drawing.ContentAlignment]::MiddleCenter
$script:lblSourceLeakDetail.ForeColor = [Drawing.Color]::DimGray
$script:grpSourceLeak.Controls.Add($script:lblSourceLeakStatus)
$script:grpSourceLeak.Controls.Add($script:lblSourceLeakDetail)
$script:lblSourceLeakDetail.BringToFront()
$leakLayout.Controls.Add($script:grpSourceLeak, 1, 0)
Reset-LeakMonitoring -DisplayState "DISCONNECTED"

$script:tabs = New-Object System.Windows.Forms.TabControl
$script:tabs.Dock = [System.Windows.Forms.DockStyle]::Fill
$script:tabs.Enabled = $false
$script:tabs.Appearance = [System.Windows.Forms.TabAppearance]::Buttons
$script:tabs.SizeMode = [System.Windows.Forms.TabSizeMode]::Fixed
$script:tabs.ItemSize = New-Object Drawing.Size(185, 42)
$script:tabs.Padding = New-Object Drawing.Point(12, 6)
$script:tabs.Font = New-Object Drawing.Font("Segoe UI", 11, [Drawing.FontStyle]::Bold)

$tabManual = New-Object System.Windows.Forms.TabPage
$tabManual.Text = "1  MANUALE"
$tabManual.Font = $form.Font
$tabManual.BackColor = [Drawing.Color]::WhiteSmoke
$script:tabs.TabPages.Add($tabManual)

$manualRight = New-Object System.Windows.Forms.FlowLayoutPanel
$manualRight.Dock = [System.Windows.Forms.DockStyle]::Fill
$manualRight.AutoScroll = $true
$manualRight.Padding = New-Object System.Windows.Forms.Padding(24)
$manualRight.FlowDirection = [System.Windows.Forms.FlowDirection]::LeftToRight
$manualRight.WrapContents = $true
$manualRight.BackColor = [Drawing.Color]::WhiteSmoke
$tabManual.Controls.Add($manualRight)

$groupTarget = New-Object System.Windows.Forms.GroupBox
$groupTarget.Text = "Nuovo target"
$groupTarget.Size = New-Object Drawing.Size(370, 235)
$groupTarget.Margin = New-Object System.Windows.Forms.Padding(12)
$manualRight.Controls.Add($groupTarget)
$groupTarget.Controls.Add((Make-Label "Pressione target (bar)" 15 28 160 24))
$script:txtTarget = New-Object System.Windows.Forms.TextBox
$script:txtTarget.Text = "0"
$script:txtTarget.Location = New-Object Drawing.Point(185, 28)
$script:txtTarget.Size = New-Object Drawing.Size(135, 24)
$groupTarget.Controls.Add($script:txtTarget)
$groupTarget.Controls.Add((Make-Label "Slew rate (bar/s)" 15 62 160 24))
$script:txtSlew = New-Object System.Windows.Forms.TextBox
$script:txtSlew.Text = "0.1"
$script:txtSlew.Location = New-Object Drawing.Point(185, 62)
$script:txtSlew.Size = New-Object Drawing.Size(135, 24)
$groupTarget.Controls.Add($script:txtSlew)
$groupTarget.Controls.Add((Make-Label "Modalita slew" 15 96 160 24))
$script:cmbSlewMode = New-Object System.Windows.Forms.ComboBox
$script:cmbSlewMode.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
[void]$script:cmbSlewMode.Items.AddRange(@("Lineare", "Maximum"))
$script:cmbSlewMode.SelectedIndex = 0
$script:cmbSlewMode.Location = New-Object Drawing.Point(185, 96)
$script:cmbSlewMode.Size = New-Object Drawing.Size(135, 24)
$groupTarget.Controls.Add($script:cmbSlewMode)
$script:chkKeepControl = New-Object System.Windows.Forms.CheckBox
$script:chkKeepControl.Text = "Mantieni CONTROL al target"
$script:chkKeepControl.Checked = $false
$script:chkKeepControl.Location = New-Object Drawing.Point(18, 132)
$script:chkKeepControl.Size = New-Object Drawing.Size(280, 25)
$groupTarget.Controls.Add($script:chkKeepControl)
$script:btnApplyTarget = Make-Button "Applica target" 18 170 145 34
$script:btnApplyTarget.BackColor = [Drawing.Color]::LightSteelBlue
$groupTarget.Controls.Add($script:btnApplyTarget)
$script:btnMeasure = Make-Button "MEASURE / STOP" 175 170 150 34
$script:btnMeasure.BackColor = [Drawing.Color]::MistyRose
$script:btnMeasure.Font = New-Object Drawing.Font("Segoe UI", 9, [Drawing.FontStyle]::Bold)
$groupTarget.Controls.Add($script:btnMeasure)

$groupAdvanced = New-Object System.Windows.Forms.GroupBox
$groupAdvanced.Text = "Parametri di pressurizzazione"
$groupAdvanced.Size = New-Object Drawing.Size(370, 280)
$groupAdvanced.Margin = New-Object System.Windows.Forms.Padding(12)
$manualRight.Controls.Add($groupAdvanced)
$groupAdvanced.Controls.Add((Make-Label "Modalita controllo" 15 28 160 24))
$script:cmbControlMode = New-Object System.Windows.Forms.ComboBox
$script:cmbControlMode.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
[void]$script:cmbControlMode.Items.AddRange(@("Active", "Passive", "Gauge"))
$script:cmbControlMode.SelectedIndex = 0
$script:cmbControlMode.Location = New-Object Drawing.Point(185, 28)
$script:cmbControlMode.Size = New-Object Drawing.Size(135, 24)
$groupAdvanced.Controls.Add($script:cmbControlMode)
$script:chkOvershoot = New-Object System.Windows.Forms.CheckBox
$script:chkOvershoot.Text = "Consenti overshoot"
$script:chkOvershoot.Checked = $false
$script:chkOvershoot.Location = New-Object Drawing.Point(18, 62)
$script:chkOvershoot.Size = New-Object Drawing.Size(250, 24)
$groupAdvanced.Controls.Add($script:chkOvershoot)
$groupAdvanced.Controls.Add((Make-Label "Tolleranza (% FS)" 15 96 160 24))
$script:txtInLimit = New-Object System.Windows.Forms.TextBox
$script:txtInLimit.Text = "0.01"
$script:txtInLimit.Location = New-Object Drawing.Point(185, 96)
$script:txtInLimit.Size = New-Object Drawing.Size(135, 24)
$groupAdvanced.Controls.Add($script:txtInLimit)
$groupAdvanced.Controls.Add((Make-Label "Tempo in-limits (s)" 15 130 160 24))
$script:numInLimitTime = New-Object System.Windows.Forms.NumericUpDown
$script:numInLimitTime.Minimum = 1
$script:numInLimitTime.Maximum = 60
$script:numInLimitTime.Value = 2
$script:numInLimitTime.Location = New-Object Drawing.Point(185, 130)
$script:numInLimitTime.Size = New-Object Drawing.Size(135, 24)
$groupAdvanced.Controls.Add($script:numInLimitTime)
$groupAdvanced.Controls.Add((Make-Label "Vent rate (bar/s)" 15 164 160 24))
$script:txtVentRate = New-Object System.Windows.Forms.TextBox
$script:txtVentRate.Text = "0.1"
$script:txtVentRate.Location = New-Object Drawing.Point(185, 164)
$script:txtVentRate.Size = New-Object Drawing.Size(135, 24)
$groupAdvanced.Controls.Add($script:txtVentRate)
$script:btnVent = Make-Button "VENT controllato" 18 210 145 34
$script:btnVent.BackColor = [Drawing.Color]::LemonChiffon
$groupAdvanced.Controls.Add($script:btnVent)
$script:btnReload = Make-Button "Rileggi parametri" 175 210 150 34
$groupAdvanced.Controls.Add($script:btnReload)

$tabIndent = New-Object System.Windows.Forms.TabPage
$tabIndent.Text = "2  INDENTING"
$tabIndent.Font = $form.Font
$tabIndent.BackColor = [Drawing.Color]::White
$script:tabs.TabPages.Add($tabIndent)
$indentTitle = Make-Label "Ciclo automatico di indenting" 30 25 500 35
$indentTitle.Font = New-Object Drawing.Font("Segoe UI", 16, [Drawing.FontStyle]::Bold)
$tabIndent.Controls.Add($indentTitle)
$indentDescription = Make-Label "Raggiunge il target alla slew impostata, mantiene CONTROL per 120 s, torna a 0 bar con la stessa slew e termina in MEASURE." 30 70 900 50
$tabIndent.Controls.Add($indentDescription)
$tabIndent.Controls.Add((Make-Label "Target (bar)" 30 145 170 28))
$script:txtIndentTarget = New-Object System.Windows.Forms.TextBox
$script:txtIndentTarget.Text = "1"
$script:txtIndentTarget.Location = New-Object Drawing.Point(210, 145)
$script:txtIndentTarget.Size = New-Object Drawing.Size(150, 25)
$tabIndent.Controls.Add($script:txtIndentTarget)
$tabIndent.Controls.Add((Make-Label "Slew rate (bar/s)" 30 185 170 28))
$script:txtIndentSlew = New-Object System.Windows.Forms.TextBox
$script:txtIndentSlew.Text = "0.1"
$script:txtIndentSlew.Location = New-Object Drawing.Point(210, 185)
$script:txtIndentSlew.Size = New-Object Drawing.Size(150, 25)
$tabIndent.Controls.Add($script:txtIndentSlew)
$script:btnStartIndent = Make-Button "Avvia indenting" 30 240 160 40
$script:btnStartIndent.BackColor = [Drawing.Color]::LightSteelBlue
$tabIndent.Controls.Add($script:btnStartIndent)
$script:btnStopIndent = Make-Button "Interrompi e MEASURE" 205 240 190 40
$script:btnStopIndent.BackColor = [Drawing.Color]::MistyRose
$tabIndent.Controls.Add($script:btnStopIndent)

$tabRoutine = New-Object System.Windows.Forms.TabPage
$tabRoutine.Text = "3  ROUTINE"
$tabRoutine.Font = $form.Font
$tabRoutine.BackColor = [Drawing.Color]::White
$tabRoutine.Padding = New-Object System.Windows.Forms.Padding(10)
$script:tabs.TabPages.Add($tabRoutine)
$routineTop = New-Object System.Windows.Forms.Panel
$routineTop.Dock = [System.Windows.Forms.DockStyle]::Top
$routineTop.Height = 74
$routineTop.BackColor = [Drawing.Color]::AliceBlue
$tabRoutine.Controls.Add($routineTop)
$routineDescription = Make-Label "Ogni riga raggiunge il target, attende In-limits e mantiene la pressione per il tempo indicato." 0 0 740 50
$routineDescription.Dock = [System.Windows.Forms.DockStyle]::Fill
$routineDescription.Padding = New-Object System.Windows.Forms.Padding(14, 5, 10, 5)
$routineDescription.Font = New-Object Drawing.Font("Segoe UI", 10, [Drawing.FontStyle]::Bold)
$routineTop.Controls.Add($routineDescription)
$script:chkRoutineKeepControl = New-Object System.Windows.Forms.CheckBox
$script:chkRoutineKeepControl.Text = "Mantieni CONTROL alla fine"
$script:chkRoutineKeepControl.Checked = $false
$script:chkRoutineKeepControl.Dock = [System.Windows.Forms.DockStyle]::Right
$script:chkRoutineKeepControl.Size = New-Object Drawing.Size(245, 32)
$script:chkRoutineKeepControl.Padding = New-Object System.Windows.Forms.Padding(5)
$routineTop.Controls.Add($script:chkRoutineKeepControl)
$script:chkRoutineKeepControl.BringToFront()

$routineBottom = New-Object System.Windows.Forms.FlowLayoutPanel
$routineBottom.Dock = [System.Windows.Forms.DockStyle]::Bottom
$routineBottom.Height = 76
$routineBottom.Padding = New-Object System.Windows.Forms.Padding(10, 12, 10, 8)
$routineBottom.WrapContents = $true
$routineBottom.AutoScroll = $true
$routineBottom.BackColor = [Drawing.Color]::Gainsboro
$tabRoutine.Controls.Add($routineBottom)
$script:btnAddStep = Make-Button "Aggiungi step" 0 0 125 38
$routineBottom.Controls.Add($script:btnAddStep)
$script:btnRemoveStep = Make-Button "Rimuovi step" 0 0 125 38
$routineBottom.Controls.Add($script:btnRemoveStep)
$script:btnSaveRoutine = Make-Button "Salva routine" 0 0 125 38
$routineBottom.Controls.Add($script:btnSaveRoutine)
$script:btnLoadRoutine = Make-Button "Carica routine" 0 0 125 38
$routineBottom.Controls.Add($script:btnLoadRoutine)
$script:btnStartRoutine = Make-Button "AVVIA ROUTINE" 0 0 155 38
$script:btnStartRoutine.BackColor = [Drawing.Color]::LightSteelBlue
$script:btnStartRoutine.Font = New-Object Drawing.Font("Segoe UI", 9, [Drawing.FontStyle]::Bold)
$routineBottom.Controls.Add($script:btnStartRoutine)
$script:btnStopRoutine = Make-Button "STOP / MEASURE" 0 0 155 38
$script:btnStopRoutine.BackColor = [Drawing.Color]::MistyRose
$script:btnStopRoutine.Font = New-Object Drawing.Font("Segoe UI", 9, [Drawing.FontStyle]::Bold)
$routineBottom.Controls.Add($script:btnStopRoutine)

$script:gridRoutine = New-Object System.Windows.Forms.DataGridView
$script:gridRoutine.Dock = [System.Windows.Forms.DockStyle]::Fill
$script:gridRoutine.AllowUserToAddRows = $true
$script:gridRoutine.AllowUserToDeleteRows = $true
$script:gridRoutine.AutoSizeColumnsMode = [System.Windows.Forms.DataGridViewAutoSizeColumnsMode]::Fill
$script:gridRoutine.RowHeadersVisible = $false
$script:gridRoutine.BackgroundColor = [Drawing.Color]::White
$script:gridRoutine.BorderStyle = [System.Windows.Forms.BorderStyle]::Fixed3D
$script:gridRoutine.ColumnHeadersHeightSizeMode = [System.Windows.Forms.DataGridViewColumnHeadersHeightSizeMode]::DisableResizing
$script:gridRoutine.ColumnHeadersHeight = 48
$script:gridRoutine.ColumnHeadersDefaultCellStyle.WrapMode = [System.Windows.Forms.DataGridViewTriState]::True
$script:gridRoutine.ColumnHeadersDefaultCellStyle.Alignment = [System.Windows.Forms.DataGridViewContentAlignment]::MiddleCenter
$script:gridRoutine.ColumnHeadersDefaultCellStyle.Font = New-Object Drawing.Font("Segoe UI", 9, [Drawing.FontStyle]::Bold)
$script:gridRoutine.RowTemplate.Height = 34
$script:gridRoutine.DefaultCellStyle.Padding = New-Object System.Windows.Forms.Padding(5, 3, 5, 3)
$script:gridRoutine.Columns.Add("Target", "Target (bar)") | Out-Null
$script:gridRoutine.Columns.Add("Slew", "Slew rate (bar/s)") | Out-Null
$script:gridRoutine.Columns.Add("Dwell", "Permanenza al target (s)") | Out-Null
$script:gridRoutine.Columns.Add("Note", "Note") | Out-Null
$script:gridRoutine.Columns[0].FillWeight = 18
$script:gridRoutine.Columns[1].FillWeight = 20
$script:gridRoutine.Columns[2].FillWeight = 25
$script:gridRoutine.Columns[3].FillWeight = 37
[void]$script:gridRoutine.Rows.Add("1", "0.1", "120", "Primo livello")
[void]$script:gridRoutine.Rows.Add("2", "0.1", "120", "Secondo livello")
[void]$script:gridRoutine.Rows.Add("0", "0.1", "0", "Ritorno a zero")
$tabRoutine.Controls.Add($script:gridRoutine)
$routineTop.BringToFront()
$routineBottom.BringToFront()

$tabSettings = New-Object System.Windows.Forms.TabPage
$tabSettings.Text = "4  IMPOSTAZIONI"
$tabSettings.Font = $form.Font
$tabSettings.BackColor = [Drawing.Color]::WhiteSmoke
$tabSettings.AutoScroll = $true
$script:tabs.TabPages.Add($tabSettings)

$groupLeakSettings = New-Object System.Windows.Forms.GroupBox
$groupLeakSettings.Text = "Soglie monitoraggio perdite"
$groupLeakSettings.Location = New-Object Drawing.Point(30, 25)
$groupLeakSettings.Size = New-Object Drawing.Size(820, 355)
$tabSettings.Controls.Add($groupLeakSettings)
$settingsDescription = Make-Label "Le stesse soglie sono applicate separatamente alla pressione del campione e alla sorgente positiva. Il calcolo usa la tendenza misurata in MEASURE." 20 25 770 48
$settingsDescription.ForeColor = [Drawing.Color]::DimGray
$groupLeakSettings.Controls.Add($settingsDescription)

$groupLeakSettings.Controls.Add((Make-Label "Calo di riferimento (bar)" 25 88 220 26))
$script:txtLeakReferenceDrop = New-Object System.Windows.Forms.TextBox
$script:txtLeakReferenceDrop.Text = $script:LeakReferenceDropBar.ToString("0.######", $script:Culture)
$script:txtLeakReferenceDrop.Location = New-Object Drawing.Point(255, 88)
$script:txtLeakReferenceDrop.Size = New-Object Drawing.Size(110, 25)
$groupLeakSettings.Controls.Add($script:txtLeakReferenceDrop)

$groupLeakSettings.Controls.Add((Make-Label "VERDE: tempo minimo (min)" 25 130 220 26))
$script:txtLeakGreenMinutes = New-Object System.Windows.Forms.TextBox
$script:txtLeakGreenMinutes.Text = $script:LeakGreenMinutes.ToString("0.###", $script:Culture)
$script:txtLeakGreenMinutes.Location = New-Object Drawing.Point(255, 130)
$script:txtLeakGreenMinutes.Size = New-Object Drawing.Size(110, 25)
$groupLeakSettings.Controls.Add($script:txtLeakGreenMinutes)

$groupLeakSettings.Controls.Add((Make-Label "GIALLO: tempo minimo (min)" 25 172 220 26))
$script:txtLeakYellowMinutes = New-Object System.Windows.Forms.TextBox
$script:txtLeakYellowMinutes.Text = $script:LeakYellowMinutes.ToString("0.###", $script:Culture)
$script:txtLeakYellowMinutes.Location = New-Object Drawing.Point(255, 172)
$script:txtLeakYellowMinutes.Size = New-Object Drawing.Size(110, 25)
$groupLeakSettings.Controls.Add($script:txtLeakYellowMinutes)

$groupLeakSettings.Controls.Add((Make-Label "ARANCIO: tempo minimo (min)" 25 214 220 26))
$script:txtLeakOrangeMinutes = New-Object System.Windows.Forms.TextBox
$script:txtLeakOrangeMinutes.Text = $script:LeakOrangeMinutes.ToString("0.###", $script:Culture)
$script:txtLeakOrangeMinutes.Location = New-Object Drawing.Point(255, 214)
$script:txtLeakOrangeMinutes.Size = New-Object Drawing.Size(110, 25)
$groupLeakSettings.Controls.Add($script:txtLeakOrangeMinutes)

$script:btnSaveLeakSettings = Make-Button "Applica e salva" 25 270 165 40
$script:btnSaveLeakSettings.BackColor = [Drawing.Color]::LightSteelBlue
$script:btnSaveLeakSettings.Font = New-Object Drawing.Font("Segoe UI", 9, [Drawing.FontStyle]::Bold)
$groupLeakSettings.Controls.Add($script:btnSaveLeakSettings)
$script:lblLeakThresholdPreview = Make-Label "" 405 88 380 185
$script:lblLeakThresholdPreview.Font = New-Object Drawing.Font("Consolas", 9)
$script:lblLeakThresholdPreview.BackColor = [Drawing.Color]::White
$script:lblLeakThresholdPreview.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$script:lblLeakThresholdPreview.Padding = New-Object System.Windows.Forms.Padding(10)
$groupLeakSettings.Controls.Add($script:lblLeakThresholdPreview)
Update-LeakSettingsPreview

$leakSettingsNote = Make-Label "Il verde viene confermato dopo l'intero intervallo verde. Allarmi piu rapidi possono comparire prima. Aumenti di pressione non sono considerati perdite." 35 400 900 48
$leakSettingsNote.ForeColor = [Drawing.Color]::DarkSlateGray
$tabSettings.Controls.Add($leakSettingsNote)

$tabLog = New-Object System.Windows.Forms.TabPage
$tabLog.Text = "LOG"
$tabLog.Font = $form.Font
$tabLog.BackColor = [Drawing.Color]::White
$script:tabs.TabPages.Add($tabLog)
$script:txtLog = New-Object System.Windows.Forms.TextBox
$script:txtLog.Dock = [System.Windows.Forms.DockStyle]::Fill
$script:txtLog.Multiline = $true
$script:txtLog.ScrollBars = [System.Windows.Forms.ScrollBars]::Both
$script:txtLog.ReadOnly = $true
$script:txtLog.Font = New-Object Drawing.Font("Consolas", 9)
$tabLog.Controls.Add($script:txtLog)

$statusPanel = New-Object System.Windows.Forms.Panel
$statusPanel.Dock = [System.Windows.Forms.DockStyle]::Bottom
$statusPanel.Height = 42
$statusPanel.BackColor = [Drawing.Color]::White
$script:lblAutomation = Make-Label "Pronto" 12 7 950 28
$script:lblAutomation.Font = New-Object Drawing.Font("Segoe UI", 9, [Drawing.FontStyle]::Bold)
$statusPanel.Controls.Add($script:lblAutomation)
$footer = Make-Label "PACE Controller v$($script:Version)" 1030 7 200 28
$footer.TextAlign = [Drawing.ContentAlignment]::MiddleRight
$statusPanel.Controls.Add($footer)

$form.Controls.Add($script:tabs)
$form.Controls.Add($statusPanel)
$form.Controls.Add($leakPanel)
$form.Controls.Add($metricsFlow)
$form.Controls.Add($connectionPanel)

$script:pollTimer = New-Object System.Windows.Forms.Timer
$script:pollTimer.Interval = 1000
$script:pollTimer.Add_Tick({ Poll-PaceData })

# ----------------------------- Eventi GUI ----------------------------------
$script:btnConnect.Add_Click({ Connect-PaceDevice })
$script:btnDisconnect.Add_Click({
    if ($script:Connected -and $script:CurrentOutputOn) {
        $answer = [System.Windows.Forms.MessageBox]::Show(
            "Il PACE e in CONTROL. Passare a MEASURE prima di disconnettere?",
            "Disconnessione",
            [System.Windows.Forms.MessageBoxButtons]::YesNoCancel,
            [System.Windows.Forms.MessageBoxIcon]::Warning,
            [System.Windows.Forms.MessageBoxDefaultButton]::Button1)
        if ($answer -eq [System.Windows.Forms.DialogResult]::Cancel) { return }
        Disconnect-PaceDevice -RequestMeasure ($answer -eq [System.Windows.Forms.DialogResult]::Yes)
    }
    else { Disconnect-PaceDevice }
})
$script:cmbModule.Add_SelectedIndexChanged({
    Reset-LeakMonitoring -DisplayState "EVALUATING"
    if ($script:Connected) { Load-DeviceSettings }
})
$script:btnReload.Add_Click({ Load-DeviceSettings })
$script:btnSaveLeakSettings.Add_Click({
    try {
        $reference = 0.0
        $green = 0.0
        $yellow = 0.0
        $orange = 0.0
        if (-not (Try-ParseUserDouble $script:txtLeakReferenceDrop.Text ([ref]$reference))) { throw "Calo di riferimento non valido." }
        if (-not (Try-ParseUserDouble $script:txtLeakGreenMinutes.Text ([ref]$green))) { throw "Tempo verde non valido." }
        if (-not (Try-ParseUserDouble $script:txtLeakYellowMinutes.Text ([ref]$yellow))) { throw "Tempo giallo non valido." }
        if (-not (Try-ParseUserDouble $script:txtLeakOrangeMinutes.Text ([ref]$orange))) { throw "Tempo arancione non valido." }
        Assert-LeakSettingsValid $reference $green $yellow $orange
        $script:LeakReferenceDropBar = $reference
        $script:LeakGreenMinutes = $green
        $script:LeakYellowMinutes = $yellow
        $script:LeakOrangeMinutes = $orange
        Save-LeakSettings
        Update-LeakSettingsPreview
        Reset-LeakMonitoring -DisplayState "EVALUATING"
        Write-AppLog "Soglie perdite aggiornate: riferimento=$reference bar; verde=$green min; giallo=$yellow min; arancio=$orange min."
        [System.Windows.Forms.MessageBox]::Show(
            "Soglie salvate. Il monitoraggio riparte con una nuova raccolta dati.",
            "Impostazioni perdite",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    }
    catch { Show-Error $_.Exception.Message }
})
$script:btnMeasure.Add_Click({
    try { Stop-Automation -SetMeasure $true } catch { Show-Error $_.Exception.Message }
})
$script:btnApplyTarget.Add_Click({
    try {
        $step = Get-ManualStep
        Start-AutomationSequence -Mode "Target manuale" -Steps @($step) `
            -KeepControlAtEnd $script:chkKeepControl.Checked
    }
    catch { Show-Error $_.Exception.Message }
})
$script:btnVent.Add_Click({
    try {
        $settings = Get-CommonPressureSettings
        $answer = [System.Windows.Forms.MessageBox]::Show(
            "Avviare il VENT alla velocita di $($settings.VentRate) bar/s? Verifica che lo scarico sia collegato in modo sicuro.",
            "Conferma VENT",
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Warning,
            [System.Windows.Forms.MessageBoxDefaultButton]::Button2)
        if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) {
            $module = Get-SelectedModule
            Reset-LeakMonitoring -DisplayState "PAUSED"
            Send-Scpi ":SOUR${module}:PRES:LEV:IMM:AMPL:VENT:UNIT 0"
            Send-Scpi ":SOUR${module}:PRES:LEV:IMM:AMPL:VENT:RATE $(Format-ScpiNumber $settings.VentRate)"
            Send-Scpi ":SOUR${module}:PRES:LEV:IMM:AMPL:VENT 1"
            Assert-NoScpiError
            Write-AppLog "VENT avviato a $($settings.VentRate) bar/s."
        }
    }
    catch { Show-Error $_.Exception.Message }
})
$script:btnStartIndent.Add_Click({
    try {
        $target = 0.0
        $slew = 0.0
        if (-not (Try-ParseUserDouble $script:txtIndentTarget.Text ([ref]$target))) { throw "Target indenting non valido." }
        if (-not (Try-ParseUserDouble $script:txtIndentSlew.Text ([ref]$slew)) -or $slew -le 0) { throw "Slew indenting non valida." }
        $steps = @(
            (New-PressureStep -Target $target -Slew $slew -Dwell 120),
            (New-PressureStep -Target 0 -Slew $slew -Dwell 0)
        )
        Start-AutomationSequence -Mode "Indenting" -Steps $steps -KeepControlAtEnd $false
    }
    catch { Show-Error $_.Exception.Message }
})
$script:btnStopIndent.Add_Click({
    try { Stop-Automation -SetMeasure $true } catch { Show-Error $_.Exception.Message }
})
$script:btnAddStep.Add_Click({ [void]$script:gridRoutine.Rows.Add("0", "0.1", "0", "") })
$script:btnRemoveStep.Add_Click({
    foreach ($row in @($script:gridRoutine.SelectedRows)) {
        if (-not $row.IsNewRow) { $script:gridRoutine.Rows.Remove($row) }
    }
})
$script:btnSaveRoutine.Add_Click({ Save-Routine })
$script:btnLoadRoutine.Add_Click({ Load-Routine })
$script:btnStartRoutine.Add_Click({
    try {
        $steps = @(Get-RoutineStepsFromGrid)
        Start-AutomationSequence -Mode "Routine" -Steps $steps `
            -KeepControlAtEnd $script:chkRoutineKeepControl.Checked
    }
    catch { Show-Error $_.Exception.Message }
})
$script:btnStopRoutine.Add_Click({
    try { Stop-Automation -SetMeasure $true } catch { Show-Error $_.Exception.Message }
})

$form.Add_Shown({
    Set-Content -LiteralPath $script:LogPath -Value "PACE Controller v$($script:Version)" -Encoding UTF8
    Write-AppLog "Applicazione avviata."
    Connect-PaceDevice
})

$form.Add_FormClosing({
    param($sender, $eventArgs)
    if ($script:ClosingHandled) { return }
    if ($script:Connected -and $script:CurrentOutputOn) {
        $answer = [System.Windows.Forms.MessageBox]::Show(
            "Il PACE e ancora in CONTROL. Passare a MEASURE prima di chiudere?",
            "Chiusura PACE Controller",
            [System.Windows.Forms.MessageBoxButtons]::YesNoCancel,
            [System.Windows.Forms.MessageBoxIcon]::Warning,
            [System.Windows.Forms.MessageBoxDefaultButton]::Button1)
        if ($answer -eq [System.Windows.Forms.DialogResult]::Cancel) {
            $eventArgs.Cancel = $true
            return
        }
        if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) {
            try { Set-MeasureMode } catch { Show-Error "Non e stato possibile impostare MEASURE: $($_.Exception.Message)" }
        }
    }
    $script:ClosingHandled = $true
    Disconnect-PaceDevice
})

try {
    [System.Windows.Forms.Application]::Run($form)
}
finally {
    if (-not $script:ClosingHandled) {
        try { if ($script:Connected) { Set-MeasureMode } } catch {}
        $script:Connected = $false
        Close-PaceSocket
        Restore-TemporaryPaceNetwork
    }
}

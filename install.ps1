[CmdletBinding()]
param(
    [Parameter()]
    [string]$TaskName = "PyStreamASR",

    [Parameter()]
    [string]$EnvFile = ".env",

    [Parameter()]
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$requiredEnvKeys = @(
    "MYSQL_DATABASE_URL",
    "MODEL_PATH",
    "APP_HOST",
    "APP_PORT",
    "APP_WORKERS"
)

function Write-Log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    Write-Host "[install] $Message"
}

function Throw-InstallError {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    throw "[install] ERROR: $Message"
}

function Resolve-ProjectPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }

    return [System.IO.Path]::GetFullPath((Join-Path -Path $PSScriptRoot -ChildPath $Path))
}

function Assert-FileExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Throw-InstallError "Missing $Description at $Path"
    }
}

function Remove-WrappingQuotes {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $trimmedValue = $Value.Trim()
    if ($trimmedValue.Length -ge 2) {
        $firstCharacter = $trimmedValue[0]
        $lastCharacter = $trimmedValue[$trimmedValue.Length - 1]
        if (($firstCharacter -eq '"' -or $firstCharacter -eq "'") -and $firstCharacter -eq $lastCharacter) {
            return $trimmedValue.Substring(1, $trimmedValue.Length - 2)
        }
    }

    return $trimmedValue
}

function Get-EnvSettings {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $settingsMap = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmedLine = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmedLine) -or $trimmedLine.StartsWith("#")) {
            continue
        }

        $separatorIndex = $trimmedLine.IndexOf("=")
        if ($separatorIndex -lt 1) {
            continue
        }

        $key = $trimmedLine.Substring(0, $separatorIndex).Trim()
        $value = $trimmedLine.Substring($separatorIndex + 1)
        $settingsMap[$key] = Remove-WrappingQuotes -Value $value
    }

    return $settingsMap
}

function Get-ValidatedRuntimeConfig {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$SettingsMap,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $missingKeys = [System.Collections.Generic.List[string]]::new()
    foreach ($key in $requiredEnvKeys) {
        if (-not $SettingsMap.ContainsKey($key) -or [string]::IsNullOrWhiteSpace([string]$SettingsMap[$key])) {
            [void]$missingKeys.Add($key)
        }
    }

    if ($missingKeys.Count -gt 0) {
        Throw-InstallError "Missing required .env keys in $Path : $($missingKeys -join ', ')"
    }

    $appHost = [string]$SettingsMap["APP_HOST"]

    try {
        $appPort = [int]$SettingsMap["APP_PORT"]
    }
    catch {
        Throw-InstallError "APP_PORT must be an integer in $Path"
    }

    try {
        $appWorkers = [int]$SettingsMap["APP_WORKERS"]
    }
    catch {
        Throw-InstallError "APP_WORKERS must be an integer in $Path"
    }

    if ([string]::IsNullOrWhiteSpace($appHost)) {
        Throw-InstallError "APP_HOST cannot be empty in $Path"
    }
    if ($appPort -lt 1 -or $appPort -gt 65535) {
        Throw-InstallError "APP_PORT must be between 1 and 65535 in $Path"
    }
    if ($appWorkers -lt 1) {
        Throw-InstallError "APP_WORKERS must be at least 1 in $Path"
    }

    return @{
        AppHost    = $appHost
        AppPort    = $appPort
        AppWorkers = $appWorkers
    }
}

function Get-PythonLauncherPath {
    $pythonLauncher = Get-Command -Name py -ErrorAction SilentlyContinue
    if ($null -eq $pythonLauncher) {
        Throw-InstallError "Python launcher 'py' was not found. Install Python 3.12 and ensure py.exe is on PATH."
    }

    try {
        & $pythonLauncher.Source -3.12 -c "import sys; print(sys.version)" | Out-Null
    }
    catch {
        Throw-InstallError "Python 3.12 is unavailable via 'py -3.12'. Install Python 3.12 before running this installer."
    }

    if ($LASTEXITCODE -ne 0) {
        Throw-InstallError "Python 3.12 is unavailable via 'py -3.12'. Install Python 3.12 before running this installer."
    }

    return $pythonLauncher.Source
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    Write-Log $Description
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        Throw-InstallError "$Description failed with exit code $LASTEXITCODE"
    }
}

function ConvertTo-SingleQuotedLiteral {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    return $Value.Replace("'", "''")
}

function Write-ServiceInstallMetadata {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Backend,

        [Parameter(Mandatory = $true)]
        [string]$ServiceName,

        [Parameter(Mandatory = $true)]
        [string]$Runtime,

        [Parameter()]
        [string]$InstallMode = "service"
    )

    $parentDirectory = Split-Path -Path $Path -Parent
    New-Item -Path $parentDirectory -ItemType Directory -Force | Out-Null

    $payload = @{
        backend      = $Backend
        service_name = $ServiceName
        runtime      = $Runtime
        install_mode = $InstallMode
    } | ConvertTo-Json

    Set-Content -LiteralPath $Path -Value $payload -Encoding UTF8
}

function Get-WindowsConsoleEntryPoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptsDirectory,

        [Parameter(Mandatory = $true)]
        [string]$PythonExecutable
    )

    $entryPointExecutable = Join-Path -Path $ScriptsDirectory -ChildPath "pystreamasr.exe"
    if (Test-Path -LiteralPath $entryPointExecutable -PathType Leaf) {
        return "@echo off`r`n`"$entryPointExecutable`" %*`r`n"
    }

    $entryPointScript = Join-Path -Path $ScriptsDirectory -ChildPath "pystreamasr-script.py"
    if (Test-Path -LiteralPath $entryPointScript -PathType Leaf) {
        return "@echo off`r`n`"$PythonExecutable`" `"$entryPointScript`" %*`r`n"
    }

    Throw-InstallError "Could not find the generated pystreamasr console entry point under $ScriptsDirectory"
}

function Ensure-UserPathContains {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory
    )

    $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathEntries = @()
    if (-not [string]::IsNullOrWhiteSpace($currentUserPath)) {
        $pathEntries = $currentUserPath.Split(";", [System.StringSplitOptions]::RemoveEmptyEntries)
    }

    foreach ($existingEntry in $pathEntries) {
        if ([string]::Equals($existingEntry.Trim(), $Directory, [System.StringComparison]::OrdinalIgnoreCase)) {
            if (-not ($env:Path.Split(";", [System.StringSplitOptions]::RemoveEmptyEntries) -contains $Directory)) {
                $env:Path = "$Directory;$env:Path"
            }
            return
        }
    }

    $updatedUserPath = if ([string]::IsNullOrWhiteSpace($currentUserPath)) {
        $Directory
    }
    else {
        "$currentUserPath;$Directory"
    }

    [Environment]::SetEnvironmentVariable("Path", $updatedUserPath, "User")
    $env:Path = "$Directory;$env:Path"
}

function Install-PyStreamASRLauncher {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptsDirectory,

        [Parameter(Mandatory = $true)]
        [string]$PythonExecutable
    )

    $launcherDirectory = Join-Path -Path $env:LOCALAPPDATA -ChildPath "PyStreamASR\bin"
    New-Item -Path $launcherDirectory -ItemType Directory -Force | Out-Null

    $wrapperPath = Join-Path -Path $launcherDirectory -ChildPath "pystreamasr.cmd"
    $wrapperContent = Get-WindowsConsoleEntryPoint -ScriptsDirectory $ScriptsDirectory -PythonExecutable $PythonExecutable
    Set-Content -LiteralPath $wrapperPath -Value $wrapperContent -Encoding ASCII

    Ensure-UserPathContains -Directory $launcherDirectory
    return $wrapperPath
}

function Register-PyStreamASRTask {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskName,

        [Parameter(Mandatory = $true)]
        [string]$RootDir,

        [Parameter(Mandatory = $true)]
        [string]$PythonExecutable,

        [Parameter(Mandatory = $true)]
        [string]$StdoutLog,

        [Parameter(Mandatory = $true)]
        [string]$StderrLog,

        [Parameter(Mandatory = $true)]
        [string]$AppHost,

        [Parameter(Mandatory = $true)]
        [int]$AppPort,

        [Parameter(Mandatory = $true)]
        [int]$AppWorkers,

        [Parameter()]
        [switch]$Force
    )

    Import-Module ScheduledTasks -ErrorAction Stop

    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

    if ($null -ne $existingTask -and -not $Force.IsPresent) {
        $response = Read-Host "Scheduled task '$TaskName' already exists. Replace it? [y/N]"
        if ($response -notmatch '^(?i:y(?:es)?)$') {
            Throw-InstallError "Cancelled because scheduled task '$TaskName' already exists. Re-run with -Force to replace it."
        }
    }

    if ($null -ne $existingTask) {
        try {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
        }
        catch {
        }

        Write-Log "Removing existing scheduled task '$TaskName'"
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    $launchCommand = "& {{ Set-Location -LiteralPath '{0}'; `$OutputEncoding = [System.Text.UTF8Encoding]::new(`$false); [Console]::InputEncoding = [System.Text.UTF8Encoding]::new(`$false); [Console]::OutputEncoding = `$OutputEncoding; `$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'; `$env:PYTHONUTF8 = '1'; `$env:PYTHONIOENCODING = 'utf-8'; & '{1}' -m uvicorn 'main:app' --host '{2}' --port '{3}' --workers '{4}' 1>> '{5}' 2>> '{6}' }}" -f `
        (ConvertTo-SingleQuotedLiteral -Value $RootDir), `
        (ConvertTo-SingleQuotedLiteral -Value $PythonExecutable), `
        (ConvertTo-SingleQuotedLiteral -Value $AppHost), `
        $AppPort, `
        $AppWorkers, `
        (ConvertTo-SingleQuotedLiteral -Value $StdoutLog), `
        (ConvertTo-SingleQuotedLiteral -Value $StderrLog)

    $actionArguments = "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$launchCommand`""
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArguments -WorkingDirectory $RootDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit ([TimeSpan]::Zero)

    Write-Log "Registering scheduled task '$TaskName' for $currentUser"
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Description "PyStreamASR FastAPI background task" `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Force | Out-Null
}

function Wait-ForHealthEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,

        [Parameter(Mandatory = $true)]
        [string]$StdoutLog,

        [Parameter(Mandatory = $true)]
        [string]$StderrLog,

        [Parameter()]
        [int]$TimeoutSeconds = 60
    )

    $healthUri = "http://127.0.0.1:$Port/health"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $healthUri -TimeoutSec 5
            if ($response.status -eq "ok") {
                return $healthUri
            }
        }
        catch {
        }

        Start-Sleep -Seconds 2
    }

    Throw-InstallError "Health check timed out for $healthUri. Inspect $StdoutLog and $StderrLog for startup details."
}

$rootDir = [System.IO.Path]::GetFullPath($PSScriptRoot)
$envFilePath = Resolve-ProjectPath -Path $EnvFile
$requirementsPath = Join-Path -Path $rootDir -ChildPath "requirements.txt"
$pyprojectPath = Join-Path -Path $rootDir -ChildPath "pyproject.toml"
$mainPath = Join-Path -Path $rootDir -ChildPath "main.py"
$venvDir = Join-Path -Path $rootDir -ChildPath "venv"
$venvPython = Join-Path -Path $venvDir -ChildPath "Scripts\python.exe"
$venvScriptsDir = Join-Path -Path $venvDir -ChildPath "Scripts"
$logsDir = Join-Path -Path $rootDir -ChildPath "logs"
$stdoutLog = Join-Path -Path $logsDir -ChildPath "scheduled_task.stdout.log"
$stderrLog = Join-Path -Path $logsDir -ChildPath "scheduled_task.stderr.log"
$installMetadataPath = Join-Path -Path $logsDir -ChildPath "service_install.json"

Assert-FileExists -Path $envFilePath -Description ".env file"
Assert-FileExists -Path $requirementsPath -Description "requirements.txt"
Assert-FileExists -Path $pyprojectPath -Description "pyproject.toml"
Assert-FileExists -Path $mainPath -Description "main.py"

$settingsMap = Get-EnvSettings -Path $envFilePath
$runtimeConfig = Get-ValidatedRuntimeConfig -SettingsMap $settingsMap -Path $envFilePath
$pythonLauncher = Get-PythonLauncherPath

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Invoke-CheckedCommand -FilePath $pythonLauncher -Arguments @("-3.12", "-m", "venv", $venvDir) -Description "Creating virtual environment"
}
else {
    Write-Log "Reusing existing virtual environment at $venvDir"
}

Invoke-CheckedCommand -FilePath $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip") -Description "Upgrading pip"
Invoke-CheckedCommand -FilePath $venvPython -Arguments @("-m", "pip", "install", "-r", $requirementsPath) -Description "Installing Python dependencies"

New-Item -Path $logsDir -ItemType Directory -Force | Out-Null
Set-Content -LiteralPath $stdoutLog -Value "" -Encoding UTF8
Set-Content -LiteralPath $stderrLog -Value "" -Encoding UTF8

Register-PyStreamASRTask `
    -TaskName $TaskName `
    -RootDir $rootDir `
    -PythonExecutable $venvPython `
    -StdoutLog $stdoutLog `
    -StderrLog $stderrLog `
    -AppHost $runtimeConfig.AppHost `
    -AppPort $runtimeConfig.AppPort `
    -AppWorkers $runtimeConfig.AppWorkers `
    -Force:$Force.IsPresent

Invoke-CheckedCommand -FilePath $venvPython -Arguments @("-m", "pip", "install", "--no-deps", "-e", $rootDir) -Description "Installing PyStreamASR console entry point"
$launcherPath = Install-PyStreamASRLauncher -ScriptsDirectory $venvScriptsDir -PythonExecutable $venvPython
Write-ServiceInstallMetadata -Path $installMetadataPath -Backend "scheduled_task" -ServiceName $TaskName -Runtime "uvicorn"

Write-Log "Starting scheduled task '$TaskName'"
Start-ScheduledTask -TaskName $TaskName
$healthUri = Wait-ForHealthEndpoint -Port $runtimeConfig.AppPort -StdoutLog $stdoutLog -StderrLog $stderrLog

@"
[install] Installed task: $TaskName
[install] Runtime: Uvicorn via $venvPython
[install] Env file: $envFilePath
[install] Health: $healthUri
[install] Stdout log: $stdoutLog
[install] Stderr log: $stderrLog
[install] Console command: $launcherPath
[install] Install metadata: $installMetadataPath
[install] Task status: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo
[install] Start task: Start-ScheduledTask -TaskName '$TaskName'
[install] Stop task: Stop-ScheduledTask -TaskName '$TaskName'
[install] Remove task: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false
"@

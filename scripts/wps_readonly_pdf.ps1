param(
    [Parameter(Mandatory=$true)][string]$InputDocx,
    [Parameter(Mandatory=$true)][string]$OutputPdf,
    [Parameter(Mandatory=$true)][string]$ResultJson,
    [Parameter(Mandatory=$true)][string]$ExpectedProgram,
    [Parameter(Mandatory=$true)][string]$ExpectedProgramSha256
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
function Get-TaskSha([string]$Path) {
    $taskStream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $taskHasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($taskHasher.ComputeHash($taskStream))).Replace('-', '').ToLower() }
    finally { $taskStream.Dispose(); $taskHasher.Dispose() }
}
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PatentWpsWindowIdentity {
    [DllImport("user32.dll", SetLastError=true)]
    public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
}
'@
$taskApplication = $null
$taskDocument = $null
$taskResult = $null
$taskResolvedInput = (Resolve-Path -LiteralPath $InputDocx).Path
$taskResolvedOutput = [IO.Path]::GetFullPath($OutputPdf)
$taskResolvedResult = [IO.Path]::GetFullPath($ResultJson)
$taskExpectedProgram = (Resolve-Path -LiteralPath $ExpectedProgram).Path
if ((Test-Path -LiteralPath $taskResolvedOutput) -or (Test-Path -LiteralPath $taskResolvedResult)) {
    throw 'Output already exists'
}
if ((Get-TaskSha $taskExpectedProgram) -ne $ExpectedProgramSha256) { throw 'Configured WPS program changed' }
$taskInputHash = Get-TaskSha $taskResolvedInput
$taskCopy = Join-Path (Split-Path -Parent $taskResolvedOutput) ('readonly-' + [guid]::NewGuid().ToString('N') + '.docx')
Copy-Item -LiteralPath $taskResolvedInput -Destination $taskCopy
if ((Get-TaskSha $taskCopy) -ne $taskInputHash) { throw 'Copy does not match input' }
try {
    $taskApplication = New-Object -ComObject KWPS.Application
    $taskDocument = $taskApplication.Documents.Open($taskCopy, $false, $true, $false)
    if (-not $taskDocument.ReadOnly) { throw 'COM document was not opened read-only' }
    $taskWindow = [IntPtr]::Zero
    try { $taskWindow = [IntPtr]([long]$taskApplication.Hwnd) } catch { }
    if ($taskWindow -eq [IntPtr]::Zero) {
        $taskWindow = [IntPtr]([long]$taskDocument.ActiveWindow.Hwnd)
    }
    [uint32]$taskProcessId = 0
    if ($taskWindow -eq [IntPtr]::Zero -or
        [PatentWpsWindowIdentity]::GetWindowThreadProcessId($taskWindow, [ref]$taskProcessId) -eq 0) {
        throw 'Cannot prove actual WPS COM host process'
    }
    $taskProcess = Get-Process -Id $taskProcessId -ErrorAction Stop
    $taskActualProgram = $taskProcess.MainModule.FileName
    if (-not [string]::Equals([IO.Path]::GetFullPath($taskActualProgram), $taskExpectedProgram, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Actual WPS COM host does not match configured program'
    }
    if ((Get-TaskSha $taskActualProgram) -ne $ExpectedProgramSha256) { throw 'Actual WPS program hash mismatch' }
    $taskIdentity = @{pid=[int]$taskProcessId; created=$taskProcess.StartTime.ToUniversalTime().ToFileTimeUtc(); executable=$taskActualProgram}
    $taskDocument.ExportAsFixedFormat($taskResolvedOutput, 17)
    if (-not (Test-Path -LiteralPath $taskResolvedOutput)) { throw 'No PDF was created' }
    if ((Get-TaskSha $taskResolvedInput) -ne $taskInputHash -or (Get-TaskSha $taskCopy) -ne $taskInputHash) {
        throw 'Input or read-only copy changed during conversion'
    }
    $taskResult = @{backend='WPS'; version=[string]$taskApplication.Version; bitness=[IntPtr]::Size*8;
        process=$taskIdentity; program_sha256=$ExpectedProgramSha256; read_only=$true; document_closed=$false;
        input=$taskResolvedInput; input_sha256=$taskInputHash; copy=$taskCopy; copy_sha256=$taskInputHash;
        pdf=$taskResolvedOutput; pdf_sha256=(Get-TaskSha $taskResolvedOutput)}
} finally {
    try {
        if ($null -ne $taskDocument) {
            try {
                $taskDocument.Close(0)
                if ($null -ne $taskResult) { $taskResult.document_closed = $true }
            } finally { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($taskDocument) }
        }
    } finally {
        if ($null -ne $taskApplication) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($taskApplication) }
    }
}
if ($null -eq $taskResult -or -not $taskResult.document_closed) { throw 'Read-only conversion did not finish' }
[IO.File]::WriteAllText($taskResolvedResult, ($taskResult | ConvertTo-Json -Depth 5 -Compress), (New-Object System.Text.UTF8Encoding($false)))

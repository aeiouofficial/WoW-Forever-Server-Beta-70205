[CmdletBinding()]
param([Parameter(Mandatory)][string]$Path)
$ErrorActionPreference = 'Stop'
$files = @(Get-ChildItem -LiteralPath $Path -Recurse -File | Where-Object Extension -in '.mmap','.mmtile')
if (-not $files.Count) { throw "No navigation data in $Path" }
$invalidCount = 0
$examples = [Collections.Generic.List[string]]::new()
foreach ($file in $files) {
    $stream = [IO.File]::OpenRead($file.FullName)
    try {
        $header = [byte[]]::new(20)
        $length = $stream.Read($header, 0, 20)
        $valid = $length -eq 20 -and [BitConverter]::ToUInt32($header,0) -eq 0x4d4d4150
        if ($file.Extension -eq '.mmap') {
            $valid = $valid -and [BitConverter]::ToUInt32($header,4) -eq 16 -and $file.Length -ge 40
        } else {
            $valid = $valid -and [BitConverter]::ToUInt32($header,8) -eq 16 -and $file.Length -eq (20 + [long][BitConverter]::ToUInt32($header,12))
        }
        if (-not $valid) {
            $invalidCount++
            if ($examples.Count -lt 5) { $examples.Add($file.FullName) }
        }
    } finally { $stream.Dispose() }
}
[ordered]@{ check='mmap-format'; path=$Path; checkedFiles=$files.Count; invalidFiles=$invalidCount; examples=@($examples) } | ConvertTo-Json -Depth 3
if ($invalidCount) { exit 1 }
exit 0

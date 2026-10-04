[CmdletBinding()]
param([Parameter(Mandatory)][string]$Path)
$ErrorActionPreference = 'Stop'
$files = @(Get-ChildItem -LiteralPath $Path -Recurse -File | Where-Object Extension -in '.vmo','.vmtree','.vmtile')
if (-not $files.Count) { throw "No collision data in $Path" }
$invalidCount = 0
$examples = [Collections.Generic.List[string]]::new()
foreach ($file in $files) {
    $stream = [IO.File]::OpenRead($file.FullName)
    try {
        $header = [byte[]]::new(8)
        $length = $stream.Read($header, 0, 8)
        $magic = [Text.Encoding]::ASCII.GetString($header)
        if ($length -ne 8 -or $magic -ne 'VMAP_4.E') {
            $invalidCount++
            if ($examples.Count -lt 10) { $examples.Add("$($file.FullName): $magic") }
        }
    } finally { $stream.Dispose() }
}
[ordered]@{ check='vmap-format'; path=$Path; checkedFiles=$files.Count; invalidFiles=$invalidCount; examples=@($examples) } | ConvertTo-Json -Depth 3
if ($invalidCount) { exit 1 }
exit 0

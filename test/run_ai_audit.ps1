param(
    [switch]$Fast,
    [switch]$UI,
    [switch]$RenderOnly,
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$argsList = @("test/bname_ai_audit_runner.py", "--keep-going")

if ($Fast) {
    $argsList += @("--profile", "standard")
} elseif ($RenderOnly) {
    $argsList += @("--profile", "render", "--include-slow")
} else {
    $argsList += @("--profile", "full", "--include-slow")
}

if ($UI) {
    $argsList += "--allow-ui"
}

if ($OutDir -ne "") {
    $argsList += @("--out-dir", $OutDir)
}

python @argsList

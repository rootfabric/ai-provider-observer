param(
    [Parameter(Position=0)]
    [string]$Command = "status",
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Rest
)
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$Root/scripts/harness/control.py" $Command @Rest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

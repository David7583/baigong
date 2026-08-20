[CmdletBinding()]
param(
    [ValidateSet('DryRun', 'Mock')]
    [string]$Mode = 'DryRun',

    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')]
    [string]$BatchName = (Get-Date -Format 'yyyyMMdd_HHmmss'),

    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Entry = Join-Path $ProjectRoot 'scripts\orchestration\action\data_action_chain_pipeline_v0006.py'
$BatchRelative = "temp/$BatchName"
$BatchRoot = Join-Path $ProjectRoot "temp\$BatchName"
$SampleSource = Join-Path $ProjectRoot 'data\data_raw\test\reliable_pipeline_fake_conversations_v0001.json'
$SampleDestination = Join-Path $BatchRoot 'data\data_raw\test\reliable_pipeline_fake_conversations_v0001.json'
$SqlConfigRelative = "$BatchRelative/sql_writer_config.yml"
$SqlConfig = Join-Path $BatchRoot 'sql_writer_config.yml'

$Arguments = @(
    $Entry,
    '--project-root', $ProjectRoot,
    '--data-root', "$BatchRelative/data",
    '--target', 'data_raw/test/reliable_pipeline_fake_conversations_v0001.json',
    '--data-intermediate-root', 'demo/intermediate',
    '--data-workspace-root', 'demo/workspace',
    '--admission-target-root', "$BatchRelative/admitted",
    '--registry-dir', "$BatchRelative/lineage",
    '--asset-version', 'v0001',
    '--data-db', "$BatchRelative/data.db",
    '--data-table', 'data_text_units',
    '--action-db', "$BatchRelative/action_data.db",
    '--sql-writer-config', $SqlConfigRelative,
    '--output-root', "$BatchRelative/outputs",
    '--structural-business-root', "$BatchRelative/structural",
    '--anchor-business-root', "$BatchRelative/anchor",
    '--derivation-business-root', "$BatchRelative/derivation",
    '--derivation-target', 'duckdb',
    '--duckdb-path', "$BatchRelative/action.duckdb",
    '--duckdb-config', 'config/duckdb_schema_config_v0001.yml',
    '--vector-target', 'both',
    '--vector-pipeline-root', "$BatchRelative/vector",
    '--chromadb-path', "$BatchRelative/chroma",
    '--vector-state-index', "$BatchRelative/state/active_index.jsonl",
    '--vector-replaced-archive', "$BatchRelative/replaced",
    '--embed-config', 'config/embedding_generator_config_v0002.yml',
    '--return-root', "$BatchRelative/returns",
    '--run-id', $BatchName,
    '--test-mode',
    '--mock-api',
    '--semantic-path', '/*/messages/*/content',
    '--semantic-limit', '5'
)

if ($Mode -eq 'DryRun') {
    $Arguments += '--dry-run'
} else {
    $Arguments += @('--init-action-db', '--confirm-database-write', '--confirm-execution')
}

if ($PlanOnly) {
    Write-Host ('Planned command: ' + $Python + ' ' + ($Arguments -join ' '))
    exit 0
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Missing .venv\Scripts\python.exe. Run launch_installer.cmd or follow README first.'
}
if (-not (Test-Path -LiteralPath $SampleSource -PathType Leaf)) {
    throw "Missing synthetic sample: $SampleSource"
}
if (Test-Path -LiteralPath $BatchRoot) {
    throw "Batch directory already exists; refusing to overwrite: $BatchRoot"
}

Get-ChildItem (Join-Path $ProjectRoot 'config') -Recurse -Filter '*.example.yml' | ForEach-Object {
    $Destination = $_.FullName -replace '\.example\.yml$', '.yml'
    if (-not (Test-Path -LiteralPath $Destination)) {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination
    }
}

New-Item -ItemType Directory -Path (Split-Path -Parent $SampleDestination) -Force | Out-Null
Copy-Item -LiteralPath $SampleSource -Destination $SampleDestination
New-Item -ItemType Directory -Path $BatchRoot -Force | Out-Null

$SqlTemplate = Join-Path $ProjectRoot 'config\sql_writer_config_v0001.example.yml'
$SqlText = Get-Content -LiteralPath $SqlTemplate -Raw
$Expected = 'path: sql/understand.db'
if (-not $SqlText.Contains($Expected)) {
    throw "Expected database path was not found; refusing an ambiguous replacement: $SqlTemplate"
}
$SqlText.Replace($Expected, "path: $BatchRelative/action_data.db") |
    Set-Content -LiteralPath $SqlConfig -Encoding UTF8

Write-Host "Mode: $Mode; isolated batch: $BatchRoot"
& $Python @Arguments
exit $LASTEXITCODE

<#
Reabre o Claude Desktop quando ele trava e não abre de novo — sem sair da
sessão do Windows, que derruba servidor e coletor do Observatório.

O erro de 22/09/2026 (Visualizador de Eventos, AppModel-Runtime):
    0x80070020: não é possível criar o contêiner do Desktop AppX para o
    pacote Claude_... devido a um erro ao converter o trabalho.
0x80070020 é ERROR_SHARING_VIOLATION: arquivo/objeto em uso. O Windows não
consegue montar o contêiner novo do app porque processos da instância
anterior (Claude.exe, auxiliares, servidores MCP que ele abriu) continuam
vivos segurando o contêiner antigo. Sair da sessão resolve porque mata
tudo — inclusive o Observatório. Este script mata só o que é do Claude.

Uso (PowerShell comum, fora do Claude):
    powershell -ExecutionPolicy Bypass -File scripts\reabrir-claude.ps1

Nunca encerra o servidor (uvicorn obs2026) nem o coletor (obs2026.cli
coletar): se algum deles estiver pendurado no Claude, avisa e para.
#>

$ErrorActionPreference = 'Stop'
$familia = 'Claude_pzs8sxrjxfjjc'
$ehObservatorio = { param($cmd) $cmd -and ($cmd -match 'obs2026') }

$todos = Get-CimInstance Win32_Process
$porId = @{}; foreach ($p in $todos) { $porId[[int]$p.ProcessId] = $p }

# Raízes: processos do pacote do Claude (instalação em WindowsApps).
$raizes = $todos | Where-Object {
    $_.Name -ieq 'claude.exe' -or
    ($_.ExecutablePath -and $_.ExecutablePath -like "*\WindowsApps\Claude_*")
}

# Descendentes das raízes (terminais, MCP, shells que o Claude abriu).
$alvo = @{}
foreach ($r in $raizes) { $alvo[[int]$r.ProcessId] = $r }
do {
    $cresceu = $false
    foreach ($p in $todos) {
        $id = [int]$p.ProcessId
        if (-not $alvo.ContainsKey($id) -and $alvo.ContainsKey([int]$p.ParentProcessId)) {
            $alvo[$id] = $p; $cresceu = $true
        }
    }
} while ($cresceu)

$obs = $alvo.Values | Where-Object { & $ehObservatorio $_.CommandLine }
if ($obs) {
    Write-Host "ATENÇÃO: processos do Observatório estão pendurados no Claude:" -ForegroundColor Yellow
    $obs | ForEach-Object { Write-Host ("  {0}  {1}" -f $_.ProcessId, $_.CommandLine) }
    Write-Host "Encerrar o Claude os derrubaria. Abra servidor e coletor numa janela" -ForegroundColor Yellow
    Write-Host "do Windows Terminal (fora do Claude) antes, ou feche-os de propósito." -ForegroundColor Yellow
    Write-Host "Nada foi encerrado."
    exit 1
}

if ($alvo.Count -eq 0) {
    Write-Host "Nenhum processo do Claude vivo."
} else {
    Write-Host ("Encerrando {0} processo(s) do Claude..." -f $alvo.Count)
    foreach ($p in $alvo.Values) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop }
        catch { }   # já saiu sozinho
    }
    $limite = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $limite -and
           (Get-Process -Id @($alvo.Keys) -ErrorAction SilentlyContinue)) {
        Start-Sleep -Milliseconds 500
    }
    $resto = Get-Process -Id @($alvo.Keys) -ErrorAction SilentlyContinue
    if ($resto) {
        Write-Host "Ainda vivos depois de 15 s:" -ForegroundColor Yellow
        $resto | Format-Table Id, ProcessName | Out-String | Write-Host
    }
}

Start-Sleep -Seconds 2
Write-Host "Abrindo o Claude..."
Start-Process "explorer.exe" "shell:AppsFolder\$familia!Claude"

# Situação do Observatório, para conferir que nada caiu.
$porta = Get-NetTCPConnection -LocalPort 8026 -State Listen -ErrorAction SilentlyContinue
$trava = 'C:\Users\micuc\Desktop\eleicao2026\observatorio2026\data\collector-oficial2026.lock'
Write-Host ""
Write-Host ("Servidor (porta 8026): " + $(if ($porta) { "no ar" } else { "FORA DO AR" }))
if (Test-Path $trava) {
    $pid_ = [int](Get-Content $trava)
    Write-Host ("Coletor: " + $(if (Get-Process -Id $pid_ -ErrorAction SilentlyContinue) { "no ar (processo $pid_)" } else { "trava órfã do processo $pid_ — use Iniciar coletor no painel" }))
} else {
    Write-Host "Coletor: parado"
}

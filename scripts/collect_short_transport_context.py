"""Read-only reconstruction of the historical short-connection timeout window."""
import datetime,hashlib,inspect,json,os,platform,re,sqlite3,subprocess,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    path=ROOT/'reports/soak_runs/0ba5ca3f83b1/results.json'
    original=json.loads(path.read_text(encoding='utf-8'));stage=original['stages'][0]
    data=Path(original['isolated_directory']).resolve()
    assert data.is_relative_to((ROOT/'data/petkit500').resolve())
    ids=[d['id'] for d in stage['details']]
    with sqlite3.connect(str(data/'control.sqlite3')) as c:
        bound=c.execute('SELECT MIN(created),MAX(updated),COUNT(*) FROM jobs WHERE id IN ('+','.join('?' for _ in ids)+')',ids).fetchone()
        rows=c.execute('SELECT id,state,created,updated FROM jobs WHERE created BETWEEN ? AND ?',(bound[0]-.02,bound[1]+.5)).fetchall()
    unexpected=[dict(id=r[0],state=r[1],created=r[2],updated=r[3]) for r in rows if r[0] not in set(ids)]
    source=inspect.getsource(urllib.request.AbstractHTTPHandler.do_open)
    evidence='except OSError as err' in source and 'raise URLError(err)' in source
    beginning=bound[0]-20;ending=bound[1]+20
    window=lambda stamp:datetime.datetime.fromtimestamp(stamp,datetime.timezone.utc).isoformat()
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    command=("$ErrorActionPreference='Stop';try {$a=[DateTimeOffset]::FromUnixTimeSeconds("+str(int(beginning))+
        ").LocalDateTime;$b=[DateTimeOffset]::FromUnixTimeSeconds("+str(int(ending))+
        ").LocalDateTime;$r=@(Get-WinEvent -FilterHashtable @{LogName='System';StartTime=$a;EndTime=$b;Level=@(1,2,3)} -ErrorAction Stop | Where-Object {$_.ProviderName -match 'tcp|net|afd|winsock'} | Select-Object TimeCreated,ProviderName,Id,LevelDisplayName);@{readable=$true;events=$r}|ConvertTo-Json -Depth 5} catch {if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {@{readable=$true;events=@()}|ConvertTo-Json} else {@{readable=$false;error_type=$_.Exception.GetType().Name}|ConvertTo-Json}}")
    event_result=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,creationflags=flags,timeout=30)
    try:events=json.loads(event_result.stdout.decode('utf-8-sig',errors='replace'))
    except ValueError:events=dict(readable=False,error_type='Event log query did not return JSON')
    ports=subprocess.run(['netsh','int','ipv4','show','dynamicport','tcp'],capture_output=True,creationflags=flags,timeout=10)
    port_values=[int(n) for n in re.findall(rb':\s*(\d+)',ports.stdout)]
    report=dict(date='2026-10-21',source_report=path.relative_to(ROOT).as_posix(),
        historical_failure=original['failure'],historical_stage_errors=stage['errors'],
        acknowledged_jobs=len(ids),persisted_acknowledged_jobs=bound[2],
        other_jobs_created_in_reconstructed_window=unexpected,
        reconstructed_window_start_utc=window(beginning),reconstructed_window_end_utc=window(ending),
        current_python=platform.python_version(),current_urllib_handler_sha256=hashlib.sha256(source.encode()).hexdigest(),
        current_urllib_wraps_request_oserrors_as_urlerror=evidence,
        inference='Current standard-library flow is consistent with connection/send failure; exact historical phase is not recorded',
        historical_python_version_not_frozen=True,proxy_bypasses_loopback_now=urllib.request.proxy_bypass('127.0.0.1'),
        current_dynamic_port_values=port_values,system_network_events=events,
        event_log_absence_does_not_prove_no_network_fault=True,historical_root_cause_confirmed=False,
        secret_headers_or_payloads_collected=False,network_configuration_modified=False)
    output=ROOT/'reports/petkit500_short_transport_forensics.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':main()

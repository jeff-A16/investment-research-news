#!/usr/bin/env python3
"""Read-only official Firecrawl MCP adapter; no browser/session bypass or account setup."""
import argparse,importlib.util,json,os,ssl,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError

URL='https://mcp.firecrawl.dev/v2/mcp'
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True);p.add_argument('--source-ids',nargs='+',required=True);p.add_argument('--tool',choices=['firecrawl_search','firecrawl_scrape'],required=True);p.add_argument('--args-file',required=True);a=p.parse_args()
 run=Path(a.run);spec=importlib.util.spec_from_file_location('log',Path(__file__).with_name('run_logger.py'));log=importlib.util.module_from_spec(spec);spec.loader.exec_module(log)
 args=json.loads(Path(a.args_file).read_text());args['objective']='Collect attributable public financial research evidence with publication dates and original source URLs.'
 if a.tool=='firecrawl_scrape':args={'formats':['markdown','html'],'onlyMainContent':True,'proxy':'basic','timeout':30000,**args}
 key=os.environ.get('FIRECRAWL_API_KEY');session=None;counter=0
 def request(method,params=None,notification=False):
  nonlocal session,counter
  counter+=1;call='firecrawl-'+uuid.uuid4().hex[:12];start=time.monotonic();body={'jsonrpc':'2.0','method':method}
  if not notification:body['id']=counter
  if params is not None:body['params']=params
  with log.locked(run):log.append_unlocked(run,'tool_start',{'call_id':call,'source_ids':a.source_ids,'tool':'Firecrawl.MCP','arguments':{'url':URL,'body':log.clean(body),'auth_mode':'environment' if key else 'keyless'}})
  headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18'}
  if key:headers['Authorization']='Bearer '+key
  if session:headers['Mcp-Session-Id']=session
  raw=b'';status=None;error=None
  try:
   try:r=urlopen(Request(URL,data=json.dumps(body).encode(),headers=headers,method='POST'),timeout=50,context=ssl.create_default_context())
   except HTTPError as exc:r=exc
   with r:
    status=r.status;raw=r.read(16*1024*1024);session=r.headers.get('Mcp-Session-Id') or session
  except (OSError,URLError,TimeoutError) as exc:error=str(exc)
  text=raw.decode('utf-8','replace')
  if key:text=text.replace(key,'[REDACTED_CREDENTIAL]')
  messages=[]
  try:messages=[json.loads(text)] if text.strip() else []
  except json.JSONDecodeError:
   for line in text.splitlines():
    if line.startswith('data:'):
     try:messages.append(json.loads(line[5:].strip()))
     except json.JSONDecodeError:pass
  business_error=any(m.get('error') or isinstance(m.get('result'),dict) and m['result'].get('isError') for m in messages)
  result=log.clean({'http_status':status,'error':error,'tool_is_error':business_error,'raw_visible_response':text,'messages':messages})
  relative='artifacts/firecrawl/'+call+'.json';data=log.canonical(result)+b'\n'
  with log.locked(run):
   log.save_new(log.target(run,relative),data);log.append_unlocked(run,'artifact_saved',{'path':relative,'sha256':log.digest(data),'bytes':len(data),'metadata':{'kind':'full available MCP response; session header excluded'},'credential_review':'key redacted before write'})
   log.append_unlocked(run,'tool_end',{'call_id':call,'status':'error' if error or status is None or status>=400 or business_error else 'success','duration_ms':round((time.monotonic()-start)*1000),'truncated':len(raw)>=16*1024*1024,'result_paths':[relative]})
  return call,result
 call,r=request('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'finance-news-publisher','version':'1.0'}})
 if r['http_status']!=200 or r['tool_is_error']:print(json.dumps({'call_id':call,'outcome':'initialization_failed','artifact':'artifacts/firecrawl/'+call+'.json'}));return 3
 request('notifications/initialized',notification=True)
 call,r=request('tools/list',{})
 advertised=[t for m in r['messages'] for t in m.get('result',{}).get('tools',[])]
 if a.tool not in [t['name'] for t in advertised]:print(json.dumps({'outcome':'tool_not_advertised','available':[t['name'] for t in advertised]}));return 3
 call,r=request('tools/call',{'name':a.tool,'arguments':args})
 documents=[]
 for m in r['messages']:
  for c in m.get('result',{}).get('content',[]):
   if c.get('type')=='text':
    try:documents.append(json.loads(c['text']))
    except json.JSONDecodeError:documents.append({'text':c['text']})
 print(json.dumps({'call_id':call,'outcome':'error' if r['tool_is_error'] or r['http_status']!=200 else 'ok','artifact':'artifacts/firecrawl/'+call+'.json','documents':[{'keys':list(d),'metadata':d.get('metadata'),'markdown_chars':len(d.get('markdown',''))} for d in documents if isinstance(d,dict)]},ensure_ascii=False))
 return 3 if r['tool_is_error'] or r['http_status']!=200 else 0
if __name__=='__main__':raise SystemExit(main())

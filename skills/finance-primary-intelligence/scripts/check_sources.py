#!/usr/bin/env python3
"""Read-only source checks with automatic audit logging. Homepage success != data access."""
import argparse
import gzip
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import socket
import ssl
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit, quote
from urllib.request import Request, urlopen
import uuid

sys.dont_write_bytecode = True


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.links, self.title_parts, self.meta = [], [], [], {}
        self.hidden, self.in_title, self.anchor = 0, False, None
        self.title_seen = False
        self.jsonld_parts, self.jsonld, self.time_candidates = None, [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script','style','noscript'):
            self.hidden += 1
        if tag=='script' and attrs.get('type','').lower()=='application/ld+json':self.jsonld_parts=[]
        if tag=='time' and attrs.get('datetime'):self.time_candidates.append(attrs['datetime'])
        if tag=='title' and not self.title_seen:
            self.in_title=True
            self.title_seen=True
        if tag=='meta':
            key=attrs.get('property') or attrs.get('name')
            if key and attrs.get('content'): self.meta[key]=attrs['content']
        if tag=='a' and attrs.get('href'):
            self.anchor={'href':attrs['href'],'text':''};self.links.append(self.anchor)

    def handle_endtag(self, tag):
        if tag=='script' and self.jsonld_parts is not None:
            try:
                value=json.loads(''.join(self.jsonld_parts))
                pending=[value]
                while pending:
                    node=pending.pop()
                    if isinstance(node,list):pending.extend(node)
                    elif isinstance(node,dict):
                        if '@graph' in node:pending.append(node['@graph'])
                        types=node.get('@type',[])
                        if isinstance(types,str):types=[types]
                        if set(types)&{'NewsArticle','Article','BlogPosting','Report','DiscussionForumPosting','SocialMediaPosting','VideoObject'}:
                            item={k:node[k] for k in ('@type','headline','name','url','datePublished','dateModified') if k in node}
                            for key in ('author','publisher'):
                                persons=node.get(key,[])
                                if not isinstance(persons,list):persons=[persons]
                                item[key]=[{k:p[k] for k in ('name','url') if k in p} if isinstance(p,dict) else p for p in persons]
                            self.jsonld.append(item)
            except (ValueError,TypeError,RecursionError):pass
            self.jsonld_parts=None
        if tag in ('script','style','noscript'): self.hidden=max(0,self.hidden-1)
        if tag=='title': self.in_title=False
        if tag=='a': self.anchor=None

    def handle_data(self, data):
        if self.jsonld_parts is not None:self.jsonld_parts.append(data)
        if self.in_title:self.title_parts.append(data)
        if not self.hidden:
            self.parts.append(data)
            if self.anchor is not None:self.anchor['text']+=data


def inspect(body,content_type,url):
    charset=re.search(r'charset=["\']?([^;\s"\']+)',content_type,re.I)
    if not charset:
        charset=re.search(br'charset\s*=\s*["\']?([a-zA-Z0-9_-]+)',body[:10000],re.I)
        encoding=charset.group(1).decode('ascii') if charset else 'utf-8'
    else: encoding=charset.group(1)
    try:text=body.decode(encoding,errors='replace')
    except LookupError:text=body.decode('utf-8',errors='replace')
    page=Page()
    try:page.feed(text)
    except Exception:pass
    visible=' '.join(' '.join(page.parts).split())
    title=' '.join(page.title_parts).strip()
    links=[]
    for a in page.links:
        link=urljoin(url,a['href'])
        if urlparse(link).scheme in ('http','https'):
            links.append({'url':link,'title':' '.join(a['text'].split())})
    return {'title':title,'visible_chars':len(visible),'visible_text':visible,
            'links':links,'metadata':page.meta,'structured_jsonld':page.jsonld,
            'datetime_candidates':page.time_candidates,'encoding':encoding}


def request_url(url):
    parts=urlsplit(url)
    if parts.username or parts.password:raise ValueError('Credential-bearing URLs are not allowed')
    return urlunsplit((parts.scheme,parts.netloc.encode('idna').decode('ascii'),
                      quote(parts.path,safe="/%:@!$&'()*+,;=-._~"),
                      quote(parts.query,safe="%=&/:?@!$'()*+,;-._~"),''))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--skill-dir',required=True)
    p.add_argument('--run',required=True)
    p.add_argument('--source-ids',nargs='*')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--timeout',type=float,default=15)
    p.add_argument('--url',help='Check a discovered detail/fallback URL for the specified source IDs')
    args=p.parse_args()
    folder,run=Path(args.skill_dir).resolve(),Path(args.run).resolve()
    spec=importlib.util.spec_from_file_location('audit_helper',folder/'scripts/run_logger.py')
    log=importlib.util.module_from_spec(spec);spec.loader.exec_module(log)
    sources=json.loads((folder/'references/sources.json').read_text())['sources']
    if args.source_ids:
        sources=[s for s in sources if s['id'] in args.source_ids]
        if set(args.source_ids)!={s['id'] for s in sources}:raise ValueError('Unknown source ID')
    if not sources:raise ValueError('No sources selected')
    if args.url and not args.source_ids:raise ValueError('--url requires explicit --source-ids')
    invocation=uuid.uuid4().hex[:12]
    def event(kind,data):
        with log.locked(run):return log.append_unlocked(run,kind,data)
    def artifact(relative,data,metadata):
        with log.locked(run):
            dest=log.target(run,relative)
            log.save_new(dest,data)
            return log.append_unlocked(run,'artifact_saved',{'path':relative,'sha256':log.digest(data),
                'bytes':len(data),'metadata':metadata,'credential_review':'public unauthenticated request; no cookies or credentials retained'})
    event('plan',{'task':'source_access_test','source_ids':[s['id'] for s in sources],
                  'workers':args.workers,'timeout':args.timeout,'url_override':args.url,
                  'limitation':'Entry access test only; successful homepage is not full-text/data/entitlement proof.'})
    def probe(s):
        sid=s['id'];url=args.url or s['entrypoint'];call_id=invocation+'-'+sid
        if not url:
            row={'source_id':sid,'source_name':s['name'],'status':'discovery_required',
                 'entrypoint':None,'checked_at':log.now(),'reason':'Dynamic category or unverified entry; needs targeted discovery, not a network failure.'}
            event('coverage',row)
            return row
        if urlparse(url).scheme not in ('http','https'):raise ValueError('Only HTTP(S) allowed')
        start=time.monotonic();at=log.now();wire_url=request_url(url)
        headers={'User-Agent':'FinancialResearchSourceCheck/1.0 (public read-only access test)',
                 'Accept':'text/html,application/json,application/pdf,text/plain;q=0.9,*/*;q=0.5',
                 'Accept-Encoding':'identity'}
        event('tool_start',{'call_id':call_id,'source_ids':[sid],'tool':'urllib.request.urlopen',
                           'started_at':at,'arguments':{'url':url,'request_url':wire_url,'method':'GET','headers':headers,'timeout':args.timeout}})
        body=b'';response_headers={};code=None;final_url=url;error=None
        try:
            req=Request(wire_url,headers=headers)
            try:response=urlopen(req,timeout=args.timeout,context=ssl.create_default_context())
            except HTTPError as exc:response=exc
            with response:
                code=response.status;final_url=response.geturl()
                response_headers={k:v for k,v in response.headers.items() if k.lower() in
                    ('content-type','content-length','date','last-modified','retry-after','location','content-encoding')}
                chunks=[]
                try:
                    while True:
                        chunk=response.read(65536)
                        if not chunk:break
                        chunks.append(chunk)
                finally:
                    body=b''.join(chunks)
        except Exception as exc:error=type(exc).__name__+': '+str(exc)
        duration=round((time.monotonic()-start)*1000)
        ctype=next((v for k,v in response_headers.items() if k.lower()=='content-type'),'')
        parse_body=body
        compression=next((v for k,v in response_headers.items() if k.lower()=='content-encoding'),'')
        if compression.lower()=='gzip':
            try:parse_body=gzip.decompress(body)
            except (OSError,EOFError) as exc:error=error or 'ResponseDecodeError: '+str(exc)
        parsed=inspect(parse_body,ctype,final_url) if parse_body and ('html' in ctype or not ctype) else {}
        title=parsed.get('title','');visible=parsed.get('visible_text','')
        if error and '[Errno 1]' in error:status='environment_network_denied'
        elif error and 'Tunnel connection failed: 403' in error:status='proxy_denied'
        elif error:status='technical_error'
        elif code==503 and b'upstream connect error' in body[:1000]:status='upstream_connection_error'
        elif code==429:status='rate_limited'
        elif code in (401,407):status='authentication_required'
        elif code==402:status='payment_required'
        elif code in (403,451):status='access_denied'
        elif code is None or code>=400:status='http_error'
        elif re.search(r'(?i)^(access denied|just a moment|security check|robot check|are you a human|captcha)|verify you are human|enable javascript and cookies|访问过于频繁|安全验证',title+' '+visible[:1000]):status='challenge_page'
        elif re.fullmatch(r'(?i)(QuikStrike Error|Application Error|Server Error)',title.strip()):status='application_error'
        elif re.search(r'(?i)页面未找到|页面不存在|page not found|^404\b',title):status='soft_not_found'
        elif 'html' in ctype and len(visible)<200:status='javascript_or_empty_shell'
        elif 'html' in ctype:status='entry_readable'
        elif 'json' in ctype or 'text/plain' in ctype:status='data_response'
        elif 'pdf' in ctype or body.startswith(b'%PDF-'):status='pdf_response'
        else:status='unclassified_response'
        row={'source_id':sid,'source_name':s['name'],'entrypoint':url,'final_url':final_url,
             'checked_at':at,'status':status,'http_status':code,'title':title,
             'content_type':ctype,'bytes':len(body),'duration_ms':duration,'error':error,
             'call_id':call_id,'full_text_verified':False,'required_data_verified':False,
             'reason':'HTTP access classification only; subject identity/content/time/entitlement still need verification.'}
        paths=[]
        if body:
            path='artifacts/probes/'+call_id+'/response.body'
            artifact(path,body,{'kind':'http_response_body','url':url,'http_status':code,'content_type':ctype});paths.append(path)
        if parsed:
            path='artifacts/probes/'+call_id+'/parsed.json'
            artifact(path,log.canonical(parsed)+b'\n',{'kind':'parsed_html','url':final_url});paths.append(path)
        path='artifacts/probes/'+call_id+'/response.json'
        artifact(path,log.canonical({'request_url':url,'final_url':final_url,'response_headers':response_headers,**row})+b'\n',{'kind':'http_result'});paths.append(path)
        event('tool_end',{'call_id':call_id,'status':'error' if error or (code and code>=400) else 'success',
                          'ended_at':log.now(),'duration_ms':duration,'truncated':False,'result_paths':paths,
                          'gaps':[] if status in ('entry_readable','data_response','pdf_response') else [status]})
        if status=='pdf_response' and shutil.which('pdftotext'):
            pdfcall=call_id+'-pdf';pdfstart=time.monotonic()
            event('tool_start',{'call_id':pdfcall,'source_ids':[sid],'tool':'pdftotext',
                               'started_at':log.now(),'arguments':{'argv':['pdftotext','-layout','-','-'],'input_artifact':paths[0]}})
            try:
                extraction=subprocess.run(['pdftotext','-layout','-','-'],input=body,capture_output=True,timeout=args.timeout)
                result={'returncode':extraction.returncode,'text':extraction.stdout.decode('utf-8',errors='replace'),
                        'stderr':extraction.stderr.decode('utf-8',errors='replace')}
                p='artifacts/probes/'+call_id+'/pdf-extraction.json'
                artifact(p,log.canonical(result)+b'\n',{'kind':'pdf_text_extraction','input_artifact':paths[0]});paths.append(p)
                event('tool_end',{'call_id':pdfcall,'status':'success' if extraction.returncode==0 else 'error',
                    'ended_at':log.now(),'duration_ms':round((time.monotonic()-pdfstart)*1000),'truncated':False,'result_paths':[p]})
                row['pdf_text_extracted']=extraction.returncode==0 and bool(result['text'].strip())
            except Exception as exc:
                event('tool_end',{'call_id':pdfcall,'status':'error','ended_at':log.now(),
                    'duration_ms':round((time.monotonic()-pdfstart)*1000),'truncated':False,
                    'result':{'error':type(exc).__name__+': '+str(exc)}})
                row['pdf_text_extracted']=False
        row['artifact_paths']=paths
        event('coverage',row)
        print(json.dumps({k:row[k] for k in ('source_id','status','http_status','title','error')},ensure_ascii=False),flush=True)
        return row
    results=[]
    # A shared environment failure is not evidence that every source host is down.
    first=next((s for s in sources if args.url or s['entrypoint']),None)
    if first:
        initial=probe(first);results.append(initial)
        sources=[s for s in sources if s['id']!=first['id']]
        if initial['status']=='environment_network_denied':
            event('gap',{'kind':'environment_network_denied','action':'Stop batch; use an authorized networking tool or approved network-enabled execution, then resume.','unattempted_source_ids':[s['id'] for s in sources]})
            for s in sources:
                row={'source_id':s['id'],'source_name':s['name'],'checked_at':log.now(),
                     'status':'not_tested_environment' if (args.url or s['entrypoint']) else 'discovery_required',
                     'reason':'Preflight socket access denied; this host was not requested.'}
                event('coverage',row);results.append(row)
            sources=[]
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,8))) as pool:
        futures={pool.submit(probe,s):s for s in sources}
        for future in as_completed(futures):results.append(future.result())
    path='checkpoints/probe-'+invocation+'.json'
    artifact(path,log.canonical({'checked_at':log.now(),'results':sorted(results,key=lambda r:r['source_id'])})+b'\n',{'kind':'source_probe_checkpoint'})
    event('checkpoint',{'path':path,'source_count':len(results)})
    print(json.dumps({'checkpoint':str(run/path),'source_count':len(results)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()

"""Neutral, immutable evidence packets sourced from original PDFs/HTTP responses.

No previous verdict, triage, graph quote or curated overlay is promoted to evidence.
"""
from collections import defaultdict
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import uuid
from urllib.parse import quote, urlparse, urljoin, urlencode
from urllib.request import Request, urlopen

from .qa_integral import connect, digest, file_hash, now, write_new

# One page of evidence never justifies spending the whole reader budget; the cut is
# visible in the text so a reader can ask for the rest instead of guessing (spec 061).
MAX_PAGE_CHARS = 7000
TRUNCATION_MARK = '\n[…truncado…]'


_WS_RUN = re.compile(r'[ \t\r\f\v]+')
_NL_RUN = re.compile(r'\n(?:[ \t]*\n)+')


def squash_ws(text):
    """Collapse runs of spaces/tabs and blank lines. MediaWiki/HTML archives and OCR
    layers carry thousands of whitespace characters that cost reader tokens and prove
    nothing; quote validation already normalises whitespace, so no evidence is lost."""
    text = _WS_RUN.sub(' ', text or '')
    return _NL_RUN.sub('\n', text).strip()


def clip_page(text, limit=MAX_PAGE_CHARS):
    text = squash_ws(text)
    return text if len(text) <= limit else text[:limit] + TRUNCATION_MARK


def packet_path(root, uid):
    revision=source_revision(root,uid)['revision']
    suffix=f'.v{revision}' if revision else ''
    return Path(root)/'packets'/f'{uid}{suffix}.json'


def source_revision(root,uid):
    db=connect(root)
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='source_revisions'").fetchone():
            row=db.execute('SELECT revision,requests FROM source_revisions WHERE unit_id=?',(uid,)).fetchone()
            if row:return {'revision':row['revision'],'requests':json.loads(row['requests'])}
        return {'revision':0,'requests':{}}
    finally:db.close()


def reopen_with_sources(root, uid, requests):
    """Append a source revision, archive BOTH judgments and restart them blindly."""
    from .qa_integral_runner import supervisor_lock
    with supervisor_lock(root):
        return _reopen_with_sources(root, uid, requests)


def _reopen_with_sources(root, uid, requests):
    root=Path(root);db=connect(root)
    if not db.execute('SELECT 1 FROM units WHERE unit_id=?',(uid,)).fetchone():
        raise ValueError('unknown unit')
    if not requests.get('documents') and not requests.get('urls'):
        raise ValueError('concrete additional sources required')
    path=root/'source-requests'/f'{uid}.json'
    old=source_revision(root,uid)
    payload={'revision':old['revision']+1,'at':now(),'requests':requests}
    version_path=path.with_name(f"{uid}.v{payload['revision']}.json")
    while version_path.exists():
        previous=json.loads(version_path.read_text())
        if previous['requests']==requests:
            payload=previous;break
        payload['revision']+=1
        version_path=path.with_name(f"{uid}.v{payload['revision']}.json")
    if not version_path.exists():write_new(version_path,payload)
    db.execute('CREATE TABLE IF NOT EXISTS source_revisions (unit_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,requests TEXT NOT NULL)')
    with db:
        for r in db.execute('SELECT * FROM readings WHERE unit_id=?',(uid,)).fetchall():
            db.execute('INSERT INTO events(at,unit_id,event,payload) VALUES (?,?,?,?)',
                       (now(),uid,'reading-superseded-by-source-revision',json.dumps(dict(r))))
        db.execute('DELETE FROM readings WHERE unit_id=?',(uid,))
        db.execute("UPDATE units SET state='pending',resolution='additional-sources-require-two-new-readers' WHERE unit_id=?",(uid,))
        db.execute('INSERT INTO source_revisions VALUES (?,?,?) ON CONFLICT(unit_id) DO UPDATE SET revision=excluded.revision,requests=excluded.requests',
                   (uid,payload['revision'],json.dumps(requests)))
    # The versioned request and old packets remain immutable. Head is runtime state.
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(payload));temp.replace(path)
    db.close()
    return payload


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.ignore=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','noscript'}: self.ignore+=1
    def handle_endtag(self,tag):
        if tag in {'script','style','noscript'}: self.ignore=max(0,self.ignore-1)
    def handle_data(self,data):
        if not self.ignore: self.parts.append(data)


def fetch_public(url, max_bytes=8_000_000, redirects=0):
    parsed=urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username:
        raise ValueError('only public HTTPS sources')
    import ipaddress, socket
    for addr in socket.getaddrinfo(parsed.hostname,443):
        if not ipaddress.ip_address(addr[4][0]).is_global:
            raise ValueError('non-public source address')
    # Redirect URLs receive the same check before a new request (no automatic redirects).
    from urllib.request import build_opener, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    from urllib.error import HTTPError
    encoded=parsed._replace(path=quote(parsed.path,safe='/%:@'),query=quote(parsed.query,safe='=&%:+,;/@?')).geturl()
    try:
        response=build_opener(NoRedirect).open(Request(encoded,headers={'User-Agent':'Entramado-QA/1.0 (+https://entramado.org)'}),timeout=25)
    except HTTPError as exc:
        if exc.code in {301,302,303,307,308} and exc.headers.get('Location') and redirects < 5:
            return fetch_public(urljoin(url,exc.headers['Location']),max_bytes,redirects+1)
        raise
    with response:
        data=response.read(max_bytes+1)
        if len(data)>max_bytes: raise ValueError('source exceeds archive limit')
        return data,response.headers.get('Content-Type','')


def archive_url(root,url,revision=None):
    root=Path(root); sid=digest(['url',url] if revision is None else ['url',url,revision]); path=root/'sources'/f'{sid}.json'
    if path.exists():
        cached=json.loads(path.read_text())
        if cached.get('status')!='inaccessible':return cached
        # Never replace a failed archive already sealed into another packet.
        # A new packet gets a fresh attempt instead of inheriting an old outage.
        return archive_url(root,url,revision={'base':revision,'retry':uuid.uuid4().hex})
    source={'source_id':sid,'url':url,'consulted_at':now(),'pages':{}}
    try:
        from urllib.error import HTTPError
        source['fetch_attempts']=[]
        for attempt in range(3):
            try:
                data,content_type=fetch_public(url)
                source['fetch_attempts'].append({'at':now(),'status':'received'})
                break
            except (OSError,HTTPError) as exc:
                source['fetch_attempts'].append({'at':now(),'error':f'{type(exc).__name__}: {exc}'})
                transient=not isinstance(exc,HTTPError) or exc.code in {408,429,500,502,503,504}
                if not transient or attempt==2:raise
        raw=root/'sources'/f'{sid}.raw';raw.parent.mkdir(parents=True,exist_ok=True);raw.write_bytes(data)
        source['sha256']=file_hash(raw)
        if data.startswith(b'%PDF'):
            import fitz
            doc=fitz.open(stream=data,filetype='pdf');source['pages']={str(i+1):page.get_text() for i,page in enumerate(doc)}
            source['format']='pdf';source['pdf_archive']=str(raw.resolve())
        else:
            html=data.decode('utf-8',errors='replace');parser=PageText();parser.feed(html)
            source['pages']={'1':' '.join(parser.parts)};source['format']='html'
        source['status']='accessible' if any(len(t.strip())>80 for t in source['pages'].values()) else 'insufficient-text'
    except Exception as exc:
        source.update(status='inaccessible',error=f'{type(exc).__name__}: {exc}')
    write_new(path,source)
    return source


class PacketBuilder:
    def __init__(self,root,repo):
        self.root,self.repo=Path(root),Path(repo)
        self.db=connect(root)
        self.nodes={r['ref']:json.loads(r['payload']) for r in self.db.execute("SELECT ref,payload FROM objects WHERE object_kind='node'")}
        self.chunks={n['props']['chunk_id']:n for n in self.nodes.values() if 'Chunk' in n.get('labels',[]) and n['props'].get('chunk_id')}
        self.incident=defaultdict(list)
        for r in self.db.execute("SELECT payload FROM objects WHERE object_kind='relationship'"):
            obj=json.loads(r['payload']);self.incident[obj['start']].append(obj);self.incident[obj['end']].append(obj)
        self.pdfs={}
        for p in (self.repo/'data').rglob('*.pdf'):
            if p.name in self.pdfs and file_hash(p) != file_hash(self.pdfs[p.name]):
                raise ValueError(f'ambiguous original PDF basename: {p.name}')
            self.pdfs[p.name]=p

    def build(self,uid):
        path=packet_path(self.root,uid)
        if path.exists():
            packet=json.loads(path.read_text())
            seal=path.with_suffix('.sha256')
            images_path=path.with_suffix('.images.json')
            if seal.exists() and seal.read_text().strip() == digest(packet) and images_path.exists():
                return packet
            if self.db.execute('SELECT 1 FROM readings WHERE unit_id=?',(uid,)).fetchone():
                raise ValueError('altered accepted source packet; investigate before resume')
            # Incomplete packet build before any accepted reader: archive every part
            # and recreate from originals. Never silently trust an orphan packet.
            archive=self.root/'interrupted-packets'/uuid.uuid4().hex;archive.mkdir(parents=True)
            for part in (path,seal,images_path):
                if part.exists():part.rename(archive/part.name)
        row=self.db.execute('SELECT u.*,o.payload,o.object_kind FROM units u JOIN objects o USING(object_id) WHERE unit_id=?',(uid,)).fetchone()
        if not row: raise ValueError('unit not in census')
        obj=json.loads(row['payload']);props=obj['props'];hints=defaultdict(set);urls=set();visited=set()
        def scan(p):
            doc=p.get('source_doc') or p.get('doc_id');page=p.get('source_page') or p.get('page_start') or 1
            if doc:
                try:hints[doc].add(int(page))
                except (ValueError,TypeError):hints[doc].add(1)
            cid=p.get('source_chunk_id')
            chunk=self.chunks.get(cid) if cid else None
            if chunk and cid not in visited:
                visited.add(cid);scan(chunk['props'])
            for k,v in p.items():
                if isinstance(v,str):
                    urls.update(re.findall(r'https://[^\s<>"\]]+',v))
        scan(props)
        neighbors=[]
        endpoints=[]
        if row['object_kind']=='relationship':
            for ref in (obj['start'],obj['end']):
                endpoint=self.nodes[ref]
                endpoints.append({**endpoint,'props':{k:v for k,v in endpoint['props'].items() if k!='embedding'}})
                scan(endpoint['props'])
        if row['object_kind']=='node':
            for r in self.incident[obj['ref']]:
                if r['type']=='MENTIONED_IN' and r['end'] in self.nodes:scan(self.nodes[r['end']]['props'])
                else:scan(r['props']);neighbors.append({k:v for k,v in r.items() if k!='ref'})
        sources=[];candidate_docs=[];images={}
        revision_info=source_revision(self.root,uid)
        requests=revision_info['requests']
        for request in requests.get('documents',[]):
            hints[request['doc']].update(int(p) for p in request['pages'])
        urls.update(requests.get('urls',[]))
        for docname,requested in sorted(hints.items()):
            candidate_docs.append({'doc':docname,'pages':sorted(requested)})
            pdf=self.pdfs.get(docname)
            if not pdf:
                sources.append({'source_id':digest(['missing',docname]),'doc':docname,'status':'missing-original','pages':{}});continue
            import fitz
            doc=fitz.open(pdf)
            selected={1}
            # A graph quote is a locator only. Search the ORIGINAL, then export
            # whole neighboring pages; never insert the quote as source text.
            if docname == props.get('source_doc') and not props.get('source_page'):
                anchor = ' '.join(str(props.get('source_quote', '')).split())[:70].casefold()
                if len(anchor) >= 16:
                    for i,page_obj in enumerate(doc):
                        if anchor in ' '.join(page_obj.get_text().split()).casefold():
                            requested.add(i+1)
            for page in sorted(requested):
                selected.update(p for p in range(max(1,page-1),min(len(doc),page+1)+1))
            # Bound one packet; all omitted locators remain visible. More context means
            # uncertainty and a follow-up packet, never silently claiming full coverage.
            # Requested pages first, then their neighbors/header. Revision requests
            # can explicitly supply the context that a previous reader lacked.
            kept=(sorted(p for p in requested if 1 <= p <= len(doc)) + sorted(selected-requested))[:18]
            sid=file_hash(pdf)
            source={'source_id':sid,'doc':docname,'sha256':sid,'status':'accessible','format':'pdf','pages':{},'page_count':len(doc),'omitted_pages':sorted(selected-set(kept))}
            for page in kept:
                source['pages'][str(page)]=doc[page-1].get_text()
                # Images are originals. Every relevant page goes to both readers equally.
                imagepath=self.root/'source-images'/f'{sid}-{page}-150dpi.png'
                if not imagepath.exists():
                    imagepath.parent.mkdir(parents=True,exist_ok=True)
                    doc[page-1].get_pixmap(dpi=150).save(imagepath)
                images[f'{sid}:{page}']=str(imagepath.resolve())
            sources.append(source)
            if not any(len(t.strip()) >= 40 for t in source['pages'].values()):
                source['status']='image-only'
            doc.close()
        revision=revision_info['revision'] or None
        for url in sorted(urls):sources.append(archive_url(self.root,url,revision))
        public={'status':'unavailable','note':'Graph snapshot is not proof of public projection.'}
        entity=props.get('entity_id') if row['object_kind']=='node' else obj.get('start_entity')
        if entity:
            apiurl='https://entramado.org/api/node/'+quote(entity,safe='')
            if row['object_kind']=='relationship' and obj.get('start_entity') and obj.get('end_entity'):
                apiurl='https://entramado.org/api/graph/relation?'+urlencode({'from':obj['start_entity'],'to':obj['end_entity'],'type':obj['type']})
            sid=digest(['public-api',apiurl]);publicpath=self.root/'public'/f'{sid}.json'
            if publicpath.exists():public=json.loads(publicpath.read_text())
            else:
                try:
                    data,_=fetch_public(apiurl)
                    public={'status':'accessible','url':apiurl,'consulted_at':now(),'response':json.loads(data)}
                except Exception as exc:public={'status':'inaccessible','url':apiurl,'error':str(exc)}
                write_new(publicpath,public)
        manifest=json.loads((self.root/'manifest.json').read_text())
        obj['props']={k:v for k,v in props.items() if k!='embedding'}
        packet={'unit_id':uid,'kind':row['kind'],'field':row['field'],'value':json.loads(row['value']),
                'graph_assertion_untrusted':obj,'neighbors_untrusted':neighbors,'endpoints_untrusted':endpoints,
                'source_candidates':candidate_docs,'sources':sources,'public_api':public,
                'public_labels_checkout':manifest['presentation'],'label_origin':'web/lib/graphStyle.ts at census; deployed bundle needs separate confirmation',
                'image_locators':list(images),'instructions':'Original PDF pages and fetched source text are evidence; graph text, quotes, summaries and labels are claims under review. No prior verdicts supplied.'}
        deployed=self.root/'deployed-presentation.json'
        if deployed.exists():
            packet['deployed_presentation']=json.loads(deployed.read_text())
            packet['label_origin']='Actual deployed JS bundle, with URL/checksum/consultation date in deployed_presentation.'
        # Avoid passing corpus/filesystem paths into reader contexts.
        for source in packet['sources']:source.pop('pdf_archive',None)
        write_new(path,packet)
        write_new(path.with_suffix('.images.json'),images)
        path.with_suffix('.sha256').write_text(digest(packet)+'\n')
        return packet

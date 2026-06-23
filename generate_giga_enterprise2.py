#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_chaos_corpus.py
========================
Erzeugt einen ABSICHTLICH chaotischen, heterogenen Enterprise-Datenkorpus zum
Stresstest eines permission-aware RAG / ACL-Tools.

NEU/komplexer gegenüber der einfachen Variante:
  * PDFs MIT aufgedrucktem Berechtigungs-Stempel (teils widersprüchlich: Kopf sagt
    INTERN, Fuß sagt STRENG VERTRAULICH) – via selbstgebautem PDF-Writer (KEINE Abhängigkeit).
  * SQL-Dumps mit echtem DDL + DCL (GRANT / REVOKE) als eigene Rechte-"Sprache".
  * Kubernetes-RBAC-YAML (Role / RoleBinding) als weiteres Modell.
  * GraphQL-Schema mit @auth-Direktiven, Postman-Collections, .http-Request-Dateien,
    .env/.ini mit Service-Accounts.
  * Encoding-Chaos (utf-8 / cp1252 / latin-1 / utf-16), kaputte/abgeschnittene JSONs,
    Müll-/Lock-/.bak-Dateien, Honeypot-Datei, Orphans ohne ACL, Duplikate mit
    UNTERSCHIEDLICHEN Rechten, Berechtigung teils NUR im Dateinamen.
  * Fileshare-Mess mit NTFS-artigen .acl-Sidecars und "broken inheritance"-Markern.

DREI feste Testanker (seed-unabhängig) -> dazu die 3 Testfragen (s. Konsole / TESTFRAGEN.md).

Reine Standardbibliothek. Aufruf z. B.:
  python generate_chaos_corpus.py --out ./enterprise_chaos --total 2500
Optionen: --total N  --pdf N  --seed N
"""

import argparse, csv, io, json, os, random, re, textwrap
from datetime import date, timedelta

# =========================================================================== #
#  Minimaler PDF-Writer (ohne externe Libs) – Text + variable Schriftgröße     #
# =========================================================================== #
def _pdf_esc(s):
    b = s.encode("cp1252", "replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

def _wrap(text, maxchars):
    out = []
    for raw in text.split("\n"):
        if not raw:
            out.append("")
            continue
        out.extend(textwrap.wrap(raw, width=maxchars) or [""])
    return out

def build_pdf(path, blocks):
    """blocks: Liste von (schriftgroesse:int, text:str). Erzeugt eine gueltige PDF."""
    pages, cur, y = [], [], 800
    for size, text in blocks:
        size = int(size)
        lead = size + 6
        for line in _wrap(text, max(8, int(990 / size))):
            if y - lead < 50:
                pages.append(cur); cur = []; y = 800
            cur.append((size, line, y)); y -= lead
    if cur: pages.append(cur)
    if not pages: pages = [[(11, "", 800)]]

    n_pages = len(pages)
    page_nums = [4 + 2 * i for i in range(n_pages)]
    cont_nums = [5 + 2 * i for i in range(n_pages)]
    total = 3 + 2 * n_pages

    objs = {}
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = b" ".join(b"%d 0 R" % p for p in page_nums)
    objs[2] = b"<< /Type /Pages /Kids [ " + kids + b" ] /Count %d >>" % n_pages
    objs[3] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
               b"/Encoding /WinAnsiEncoding >>")
    for i, pg in enumerate(pages):
        pn, cn = page_nums[i], cont_nums[i]
        objs[pn] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                    b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % cn)
        ops = bytearray(b"BT\n")
        for size, line, ypos in pg:
            ops += b"/F1 %d Tf\n1 0 0 1 50 %d Tm\n" % (size, ypos)
            ops += b"(" + _pdf_esc(line) + b") Tj\n"
        ops += b"ET"
        objs[cn] = b"<< /Length %d >>\nstream\n" % len(ops) + bytes(ops) + b"\nendstream"

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = {}
    for n in range(1, total + 1):
        offs[n] = len(out)
        out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (total + 1)
    out += b"0000000000 65535 f \n"
    for n in range(1, total + 1):
        out += b"%010d 00000 n \n" % offs[n]
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (total + 1, xref))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(out)

# =========================================================================== #
#  Stammdaten                                                                  #
# =========================================================================== #
COMPANY = "Mustermann Industrie GmbH"
DNS = "musterindustrie.local"
TENANT = "MUSTERINDUSTRIE"

DOMAINS = [
    ("Vertrieb", "VK"), ("Finanzen", "FI"), ("Personal", "HR"), ("Einkauf", "EK"),
    ("Produktion", "PP"), ("IT", "IT"), ("Marketing", "MK"), ("Recht", "RE"),
    ("Geschaeftsfuehrung", "GF"), ("F_und_E", "FE"),
]
TABLES = {
    "VK": ["kunden", "auftraege", "umsatz", "leads"],
    "FI": ["hauptbuch", "debitoren", "kreditoren", "kostenstellen"],
    "HR": ["mitarbeiter", "gehaelter", "urlaub", "bewerber"],
    "EK": ["lieferanten", "bestellungen", "preise", "vertraege"],
    "PP": ["maschinen", "fortschritt", "ausschuss", "stueckliste"],
    "IT": ["assets", "lizenzen", "tickets", "serviceaccounts"],
    "MK": ["kampagnen", "budget", "leads", "events"],
    "RE": ["vertraege", "fristen", "nda", "marken"],
    "GF": ["beteiligungen", "boni", "kpi", "planung"],
    "FE": ["projekte", "patente", "versuche", "budget"],
}
FIRST = ["Anna","Thomas","Markus","Julia","Sabine","Stefan","Nadine","Jan","Petra","Kevin",
         "Lena","Michael","Claudia","Tobias","Birgit","Olaf","Yusuf","Mei","Sven","Ines"]
LAST = ["Müller","Schmidt","Weber","Wagner","Becker","Hofmann","Krause","Lang","Schäfer",
        "Bauer","Koch","Richter","Klein","Wolff","Neumann","Yilmaz","Nguyen","Braun"]
TITLES = ["Sachbearbeiter:in","Teamleitung","Abteilungsleitung","Werkstudent:in",
          "Praktikant:in","Controller:in","Admin","Geschäftsführung","Datenschutzbeauftragte:r"]
CLASS = [("OEFFENTLICH","Öffentlich"),("INTERN","Intern"),
         ("VERTRAULICH","Vertraulich"),("STRENG_VERTRAULICH","Streng vertraulich – nur GF")]
SCUFF = ["","","_final","_FINAL_v2"," (1)","_alt","_kopie","_NEU","_bitte_nicht_loeschen",
         "_v3_final_final","_ENTWURF","__DRAFT__"]
PERM_MODELS = ["ActiveDirectory","SharePoint","SAP","POSIX","Inline","SQL_GRANT","K8s_RBAC"]

def person(r): return f"{r.choice(FIRST)} {r.choice(LAST)}"
def mail(name):
    a, b = name.lower().split(" ")[:2]
    tr = lambda s: s.replace("ü","ue").replace("ö","oe").replace("ä","ae").replace("ß","ss")
    return f"{tr(a)[0]}.{tr(b)}@{DNS}"
def sid(r): return "S-1-5-21-%d-%d-%d-%d" % (r.randint(1,4*10**9), r.randint(1,4*10**9),
                                             r.randint(1,4*10**9), r.randint(1000,9999))
def rdate(r, s=date(2021,1,1), e=date(2025,11,1)):
    return s + timedelta(days=r.randint(0, (e-s).days))
def de(d): return d.strftime("%d.%m.%Y")
def safe(n): return re.sub(r'[\\/:*?"<>|]', "_", n).strip().rstrip(".") or "datei"

# =========================================================================== #
#  Generator-Zustand                                                           #
# =========================================================================== #
class Gen:
    def __init__(self, out, r):
        self.out, self.r = out, r
        self.files = 0
        self.seq = 0
        self.manifest = []          # (relpath, kind, model, classification)
        self.decisions = []         # beschluss-IDs für Lineage
    def nid(self):
        self.seq += 1; return self.seq
    def p(self, *a): return os.path.join(self.out, *a)
    def add(self, rel, kind, model="-", cls="-"):
        self.files += 1; self.manifest.append((rel, kind, model, cls))

def wtext(path, text, enc="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=enc, errors="replace", newline="") as f: f.write(text)
def wbytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f: f.write(data)
def wjson(path, obj, broken=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    if broken:  # absichtlich abgeschnitten -> Parser-Robustheit
        s = s[: int(len(s) * 0.7)]
    with open(path, "w", encoding="utf-8") as f: f.write(s)

# =========================================================================== #
#  Berechtigungs-Erzeuger (7 Modelle)                                          #
# =========================================================================== #
def make_perm(r, label, code, model=None):
    model = model or r.choice(PERM_MODELS)
    if model == "ActiveDirectory":
        aces = []
        for _ in range(r.randint(1,3)):
            sc = r.choice(["RW","RO","ADMIN"])
            aces.append({"principal": f"{TENANT}\\GG-{code}-{sc}", "sid": sid(r),
                         "rights": r.choice(["FullControl","Modify","ReadAndExecute","Read"]),
                         "type": r.choice(["Allow","Allow","Allow","Deny"]),
                         "inherited": r.random()<0.6})
        return {"model":"ActiveDirectory","object":label,
                "owner":f"{TENANT}\\svc-{code.lower()}","aces":aces}
    if model == "SharePoint":
        ras=[]
        for _ in range(r.randint(1,3)):
            k=r.choice(["SharePointGroup","User","Claim"])
            if k=="User": pr=f"i:0#.f|membership|{mail(person(r))}"
            elif k=="Claim": pr="Jeder außer externe Benutzer"
            else: pr=f"{r.choice(['Mitglieder','Besitzer','Besucher'])} {code}"
            ras.append({"principal":pr,"type":k,"level":r.choice(
                ["Vollzugriff","Bearbeiten","Mitwirken","Lesen"])})
        return {"model":"SharePoint","siteUrl":f"https://{DNS}/sites/{code}",
                "item":label,"hasUniquePermissions":r.random()<0.4,"roleAssignments":ras}
    if model == "SAP":
        return {"model":"SAP","object":label,"client":"100",
                "roles":[f"Z_{code}_{r.choice(['ANZEIGE','PFLEGE','ADMIN'])}_1000"],
                "authObjects":r.sample(["S_TABU_DIS","F_BKPF_BUK","M_BEST_EKO","P_ORGIN"],
                                       k=r.randint(1,2)),
                "tcodes":r.sample(["FB03","VA03","ME23N","PA20","SE16N"],k=r.randint(1,2))}
    if model == "POSIX":
        return {"model":"POSIX","object":label,
                "mode":r.choice(["-rw-r-----","-rw-rw----","-rw-r--r--","-rwxr-x---"]),
                "owner":f"svc-{code.lower()}","group":f"{code.lower()}-{r.choice(['rw','ro'])}"}
    if model == "SQL_GRANT":
        return {"model":"SQL_GRANT","object":label,
                "grants":[{"role":f"{code.lower()}_{r.choice(['ro','rw','admin'])}",
                           "priv":r.choice(["SELECT","SELECT, INSERT, UPDATE","ALL PRIVILEGES"])}]}
    if model == "K8s_RBAC":
        return {"model":"K8s_RBAC","object":label,
                "role":f"{code.lower()}-{r.choice(['reader','editor'])}",
                "verbs":r.choice([["get","list"],["get","list","watch"],
                                  ["get","list","create","update"]]),
                "subjects":[f"group:{code.lower()}-{r.choice(['team','leads'])}"]}
    code2,label2 = r.choice(CLASS)
    return {"model":"Inline","object":label,"classification":code2,
            "classification_label":label2,
            "berechtigte":[r.choice(TITLES) for _ in range(r.randint(1,3))]}

def perm_banner(perm):
    """Klartext-Banner für Dokumente/PDF-Stempel."""
    m = perm["model"]
    if m=="Inline":
        return (f"BERECHTIGUNG: {perm['classification_label']}\n"
                f"Berechtigte: {', '.join(perm['berechtigte'])}")
    if m=="ActiveDirectory":
        a=perm["aces"][0]; return f"BERECHTIGUNG (AD): {a['principal']} [{a['rights']}/{a['type']}]"
    if m=="SharePoint":
        ra=perm["roleAssignments"][0]; return f"BERECHTIGUNG (SharePoint): {ra['principal']} -> {ra['level']}"
    if m=="SAP":
        return f"BERECHTIGUNG (SAP): Rolle {perm['roles'][0]} / TA {', '.join(perm['tcodes'])}"
    if m=="POSIX":
        return f"BERECHTIGUNG (POSIX): {perm['mode']} {perm['owner']}:{perm['group']}"
    if m=="SQL_GRANT":
        g=perm["grants"][0]; return f"BERECHTIGUNG (SQL): GRANT {g['priv']} TO {g['role']}"
    if m=="K8s_RBAC":
        return f"BERECHTIGUNG (K8s-RBAC): role {perm['role']} verbs={perm['verbs']}"
    return "BERECHTIGUNG: (nicht definiert)"

# =========================================================================== #
#  1) Datenbanken: SQLite + SQL-Dump(GRANT) + CSV(Encoding-Mix) + Sidecar      #
# =========================================================================== #
import sqlite3
def gen_db_bundle(g):
    r=g.r; name,code = r.choice(DOMAINS)
    tbls = r.sample(TABLES[code], k=r.randint(1, min(3,len(TABLES[code]))))
    uid=g.nid(); base=safe(f"{r.choice(tbls)}_{r.randint(2021,2025)}_{uid:04d}{r.choice(SCUFF)}")
    dbrel=os.path.join("01_datenbanken",name,base+r.choice([".db",".sqlite"]))
    perm=make_perm(r,base,code)
    os.makedirs(os.path.dirname(g.p(dbrel)),exist_ok=True)
    con=sqlite3.connect(g.p(dbrel)); cur=con.cursor()
    for t in tbls:
        cur.execute(f'CREATE TABLE IF NOT EXISTS "{t}" (id INTEGER PRIMARY KEY,'
                    f'bezeichnung TEXT,wert REAL,verantwortlich TEXT,stand TEXT)')
        for i in range(r.randint(3,12)):
            cur.execute(f'INSERT INTO "{t}" VALUES (?,?,?,?,?)',
                        (i+1,f"{t}_pos_{i+1}",round(r.uniform(10,99999),2),
                         person(r),de(rdate(r))))
    place=r.random()
    pmodel=perm["model"]
    if place<0.4:
        cur.execute("CREATE TABLE _acl(objekt TEXT,modell TEXT,prinzipal TEXT,recht TEXT,"
                    "effekt TEXT,geerbt INT)")
        for row in _acl_rows(perm): cur.execute("INSERT INTO _acl VALUES(?,?,?,?,?,?)",row)
    elif place<0.7:
        side=g.p(dbrel)+".acl.json"; wjson(side,perm); 
        g.add(os.path.relpath(side,g.out),"acl-sidecar",pmodel)
    else:
        pmodel="ORPHAN"
    con.commit(); con.close()
    g.add(dbrel,"sqlite-db",pmodel)

    # SQL-Dump mit DDL + DCL (GRANT/REVOKE) – eigene Rechte-"Sprache"
    if r.random()<0.6:
        t=r.choice(tbls); role=f"{code.lower()}_{r.choice(['ro','rw','analyst'])}"
        sql=(f"-- Export {COMPANY} · {de(rdate(r))}\n-- {perm_banner(perm)}\n"
             f"CREATE TABLE {t} (id INT PRIMARY KEY, bezeichnung TEXT, wert NUMERIC,"
             f" verantwortlich TEXT);\n"
             f"GRANT {r.choice(['SELECT','SELECT, INSERT','ALL PRIVILEGES'])} ON {t} TO {role};\n")
        if r.random()<0.4:
            sql+=f"REVOKE INSERT ON {t} FROM {code.lower()}_praktikant; -- nach Audit entzogen\n"
        srel=os.path.join("01_datenbanken",name,f"ddl_{t}_{g.nid():04d}.sql")
        wtext(g.p(srel),sql); g.add(srel,"sql-dump","SQL_GRANT")

    # CSV-Export, Encoding-Chaos
    if r.random()<0.8:
        t=r.choice(tbls); enc=r.choice(["utf-8","utf-8","cp1252","latin-1"])
        buf=io.StringIO(); w=csv.writer(buf,delimiter=";")
        if perm["model"]=="Inline":
            w.writerow([f"# {perm['classification_label']}"])
        w.writerow(["id","bezeichnung","wert","verantwortlich","stand"])
        for i in range(r.randint(3,10)):
            w.writerow([i+1,f"{t}_pos_{i+1}",round(r.uniform(10,9999),2),person(r),de(rdate(r))])
        crel=os.path.join("01_datenbanken",name,f"export_{t}_{g.nid():04d}.csv")
        wtext(g.p(crel),buf.getvalue(),enc=enc); g.add(crel,f"csv({enc})",perm["model"])
    return base,code,perm

def _acl_rows(perm):
    m=perm["model"]; out=[]
    if m=="ActiveDirectory":
        for a in perm["aces"]:
            out.append((perm["object"],m,a["principal"],a["rights"],a["type"],int(a["inherited"])))
    elif m=="SharePoint":
        for ra in perm["roleAssignments"]:
            out.append((perm["item"],m,ra["principal"],ra["level"],"Allow",
                        0 if perm["hasUniquePermissions"] else 1))
    elif m=="SAP":
        for ro in perm["roles"]:
            out.append((perm["object"],m,ro,",".join(perm["tcodes"]),"Allow",0))
    elif m=="POSIX":
        out.append((perm["object"],m,f"{perm['owner']}:{perm['group']}",perm["mode"],"Allow",0))
    elif m=="SQL_GRANT":
        gx=perm["grants"][0]; out.append((perm["object"],m,gx["role"],gx["priv"],"Allow",0))
    elif m=="K8s_RBAC":
        out.append((perm["object"],m,perm["subjects"][0],",".join(perm["verbs"]),"Allow",0))
    else:
        out.append((perm["object"],m,",".join(perm.get("berechtigte",[])),
                    perm.get("classification","INTERN"),"Allow",0))
    return out

# =========================================================================== #
#  2) APIs: OpenAPI(JSON/YAML) + GraphQL + Postman + .http + .env              #
# =========================================================================== #
def gen_api(g):
    r=g.r; name,code=r.choice(DOMAINS); res=r.choice(TABLES[code]); uid=g.nid()
    base=safe(f"{code.lower()}-{res}-api-{uid:04d}"); d=os.path.join("02_api",name)
    scopes=[f"{code.lower()}:{s}" for s in ("read","write","admin")]
    spec={"openapi":"3.0.1","info":{"title":f"{name} {res} API","version":f"{r.randint(1,4)}.0"},
          "servers":[{"url":f"https://api.{DNS}/{code.lower()}/v1"}],
          "x-data-source":f"db://{code.lower()}/{res}",
          "x-permissions":{"model":"OAuth2-Scopes","scopes":{s:r.choice(TITLES) for s in scopes}},
          "paths":{f"/{res}":{"get":{"security":[{"oauth":[scopes[0]]}]}}}}
    srel=os.path.join(d,base+".openapi.json"); wjson(g.p(srel),spec,
        broken=r.random()<0.05); g.add(srel,"api-spec","OAuth2-Scopes")

    if r.random()<0.4:  # GraphQL mit @auth
        gql=(f'"""{name} {res} – Schema (auth via Direktive)"""\n'
             f'directive @auth(scope: String!) on FIELD_DEFINITION\n'
             f'type {res.capitalize()} {{ id: ID! bezeichnung: String wert: Float '
             f'@auth(scope: "{scopes[1]}") }}\n'
             f'type Query {{ {res}(id: ID!): {res.capitalize()} @auth(scope: "{scopes[0]}") }}\n')
        grel=os.path.join(d,f"{base}.graphql"); wtext(g.p(grel),gql); g.add(grel,"graphql","GraphQL@auth")

    if r.random()<0.35:  # Postman-Collection
        pm={"info":{"name":f"{name} {res}"},"auth":{"type":"oauth2",
             "oauth2":[{"key":"scope","value":scopes[0]}]},
            "item":[{"name":f"GET {res}","request":{"method":"GET",
              "header":[{"key":"Authorization","value":"Bearer {{token}}"}],
              "url":f"https://api.{DNS}/{code.lower()}/v1/{res}"}}]}
        prel=os.path.join(d,f"{base}.postman_collection.json"); wjson(g.p(prel),pm)
        g.add(prel,"postman","OAuth2-Scopes")

    if r.random()<0.3:  # .http Request-Datei
        ht=(f"### {name} {res}\n# benoetigt scope: {scopes[0]}\n"
            f"GET https://api.{DNS}/{code.lower()}/v1/{res}\n"
            f"Authorization: Bearer {{{{token_{code.lower()}}}}}\n")
        hrel=os.path.join(d,f"{base}.http"); wtext(g.p(hrel),ht); g.add(hrel,"http-req","OAuth2-Scopes")

    if r.random()<0.5:  # .env / Service-Account
        svc=f"svc-{code.lower()}-api"
        env=(f"# {COMPANY} – NICHT einchecken!\n"
             f"DB_HOST=db-{code.lower()}.{DNS}\nDB_NAME={code.lower()}_prod\n"
             f"SERVICE_ACCOUNT={svc}\nDB_ROLE={r.choice(['readonly','readwrite','owner'])}\n"
             f"DB_PASSWORD=${{VAULT:{svc}/pw}}  # Referenz, kein Klartext\n")
        erel=os.path.join(d,f".env.{code.lower()}.{g.nid():04d}"); wtext(g.p(erel),env)
        g.add(erel,"env-config","ServiceAccount")

# =========================================================================== #
#  3) Meetings (md/txt + manche als PDF) mit Beschlüssen                       #
# =========================================================================== #
GREMIEN=["Daten-Governance-Board","IT-Security-Jour-Fixe","Abteilungsleiter-Runde",
         "Datenschutz-Komitee","Projekt-Steuerkreis","GF-Sitzung"]
def gen_meeting(g, db_refs):
    r=g.r; name,code=r.choice(DOMAINS); d=rdate(r); grem=r.choice(GREMIEN)
    att=[(person(r),r.choice(TITLES)) for _ in range(r.randint(3,7))]
    perm=make_perm(r,f"protokoll_{code}",code,model="Inline")
    lines=[perm_banner(perm),"", f"# Protokoll {grem} – {name}",
           f"Datum: {de(d)}   Ort: {COMPANY}", f"Protokoll: {person(r)}","","## Teilnehmende"]
    for nm,ti in att: lines.append(f"- {nm} ({ti}) <{mail(nm)}>")
    lines+=["","## Beschlüsse"]
    for _ in range(r.randint(1,4)):
        bid=f"BESCHLUSS-{d.year}-{r.randint(1,9999):04d}"; g.decisions.append(bid)
        if db_refs and r.random()<0.85:
            obj,oc,_=r.choice(db_refs); ref=f"Tabelle/DB '{obj}'"
        else: ref=f"API '{code.lower()}-api'"
        act=r.choice(["GEWAEHRT","ENTZUG","AENDERUNG","NEU_ROLLE","FRIST"])
        who=r.choice([f"AD-Gruppe {TENANT}\\GG-{code}-{r.choice(['RW','RO'])}",
                      person(r),f"SAP-Rolle Z_{code}_ANZEIGE_1000",f"Abteilung {name}"])
        lines.append(f"- **{bid}** [{act}]: Zugriff auf {ref} für {who} "
                     f"({r.choice(['Lesen','Schreiben','Vollzugriff'])}).")
    text="\n".join(lines)
    fname=safe(f"{de(d)}_{grem}_{name}_{g.nid():04d}{r.choice(SCUFF)}")
    rel_dir=os.path.join("03_meetings",str(d.year))
    if r.random()<0.2:  # als PDF mit Stempel
        rel=os.path.join(rel_dir,fname+".pdf")
        blocks=[(18,perm_banner(perm).split("\n")[0]),(11,text)]
        build_pdf(g.p(rel),blocks); g.add(rel,"meeting-pdf","Inline",perm["classification"])
    else:
        ext=r.choice([".md",".md",".txt"]); rel=os.path.join(rel_dir,fname+ext)
        wtext(g.p(rel),text); g.add(rel,"meeting"+ext,"Inline",perm["classification"])

# =========================================================================== #
#  4) PDFs mit Berechtigungs-Stempel (teils WIDERSPRÜCHLICH)                   #
# =========================================================================== #
def gen_pdf_doc(g):
    r=g.r; name,code=r.choice(DOMAINS); perm=make_perm(r,f"doc_{code}",code)
    topic=r.choice(["Quartalsbericht","Gehaltsuebersicht","Vertragsentwurf","Audit-Notiz",
                    "Kostenstellen-Report","Patentskizze","Bonusliste","Risikobewertung"])
    head=perm_banner(perm)
    # 20% widersprüchlicher Fußzeilen-Stempel
    contradict = r.random()<0.2
    foot = perm_banner(make_perm(r,"x",code,model="Inline")) if contradict else head
    body=[f"{COMPANY} – {topic} {name}",
          f"Erstellt: {de(rdate(r))} von {person(r)}",""]
    for _ in range(r.randint(6,16)):
        body.append(f"  - {r.choice(TABLES[code])}: Wert {round(r.uniform(100,99999),2)} € "
                    f"(verantw. {person(r)})")
    blocks=[(20,">> "+head.split(chr(10))[0]+" <<"),(10,head),(12,""),
            (11,"\n".join(body)),(12,""),(16,"-- "+foot.split(chr(10))[0]+" --")]
    # Berechtigung teils NUR im Dateinamen
    name_stamp = ""
    if perm["model"]=="Inline" and r.random()<0.5:
        name_stamp = "__"+perm["classification"].replace("_","-")
    rel=os.path.join("04_pdf_dokumente",
                     safe(f"{topic}_{name}_{g.nid():04d}{name_stamp}{r.choice(SCUFF)}")+".pdf")
    build_pdf(g.p(rel),blocks)
    cls = perm.get("classification","-") if perm["model"]=="Inline" else "-"
    g.add(rel,"pdf-stamped"+("(widerspruch)" if contradict else ""),perm["model"],cls)

# =========================================================================== #
#  5) Random Noise / Honeypot / kaputte Dateien                                #
# =========================================================================== #
def gen_noise(g):
    r=g.r; nd="05_random_noise"; kind=r.choice(
        ["log","lock","bak","blob","ds_store","thumbs","tmp","readme_wrong","utf16export"])
    if kind=="log":
        lines=[f"{de(rdate(r))} {r.choice(['INFO','WARN','ERROR'])} svc-{r.choice(['fi','hr','it'])} "
               f"access check user={person(r).split()[0].lower()} -> "
               f"{r.choice(['GRANTED','DENIED'])}" for _ in range(r.randint(20,80))]
        rel=os.path.join(nd,f"app_{g.nid():04d}.log"); wtext(g.p(rel),"\n".join(lines))
        g.add(rel,"log","-")
    elif kind=="lock":
        rel=os.path.join(nd,f"~$bericht_{g.nid():04d}.docx.lock"); wtext(g.p(rel),
            f"locked by {mail(person(r))}"); g.add(rel,"lockfile","-")
    elif kind=="bak":
        rel=os.path.join(nd,f"dump_{g.nid():04d}.bak"); wtext(g.p(rel),
            "BACKUP "+",".join(str(r.randint(0,9)) for _ in range(200))); g.add(rel,"bak","-")
    elif kind=="blob":
        rel=os.path.join(nd,f"blob_{g.nid():04d}.bin"); wbytes(g.p(rel),
            bytes(r.randrange(256) for _ in range(r.randint(64,512)))); g.add(rel,"binary","-")
    elif kind=="ds_store":
        rel=os.path.join(nd,".DS_Store"); wbytes(g.p(rel),b"\x00\x00\x00\x01Bud1"); 
        g.add(rel,"junk","-")
    elif kind=="thumbs":
        rel=os.path.join(nd,"Thumbs.db"); wbytes(g.p(rel),b"\xd0\xcf\x11\xe0junk"); 
        g.add(rel,"junk","-")
    elif kind=="tmp":
        rel=os.path.join(nd,f"tmp_{g.nid():04d}.tmp"); wtext(g.p(rel),"temp"); g.add(rel,"tmp","-")
    elif kind=="utf16export":
        rel=os.path.join(nd,f"win_export_{g.nid():04d}.txt")
        wtext(g.p(rel),f"Bezeichnung\tWert\nMüller GmbH\t{r.randint(100,9999)}\n",enc="utf-16")
        g.add(rel,"utf16-export","-")
    else:  # readme_wrong: behauptet falsche Rechte (Ablenkung)
        rel=os.path.join(nd,f"LIESMICH_{g.nid():04d}.txt")
        wtext(g.p(rel),"Hinweis: Diese Datei enthält KEINE gueltigen Berechtigungen. "
                       "Bitte ignorieren. (Ablenkungs-/Rauschdatei)\n"); g.add(rel,"noise-readme","-")

# =========================================================================== #
#  6) Fileshare-Mess mit NTFS-artigen .acl + broken inheritance + Duplikate    #
# =========================================================================== #
def gen_share(g, db_refs):
    r=g.r; name,code=r.choice(DOMAINS)
    sub=os.path.join("07_shares",f"{code}$",r.choice(["Allgemein","Projekte","Vertraulich",
                                                      "Archiv 2023","_temp"]))
    fn=safe(f"{r.choice(['Notiz','Liste','Plan','Vorlage'])}_{g.nid():04d}{r.choice(SCUFF)}.txt")
    perm=make_perm(r,fn,code)
    rel=os.path.join(sub,fn); wtext(g.p(rel),
        f"{perm_banner(perm)}\n\nInhalt: {r.choice(TABLES[code])} – {person(r)}\n")
    g.add(rel,"share-file",perm["model"])
    # NTFS-artiger .acl-Sidecar + inheritance marker
    acl=g.p(rel)+".acl"
    inh = "PROTECTED(broken)" if r.random()<0.3 else "inherited"
    wtext(acl,json.dumps({"model":"NTFS","path":rel,"inheritance":inh,
        "ace":_acl_rows(perm)},ensure_ascii=False,indent=2)); 
    g.add(os.path.relpath(acl,g.out),"ntfs-acl",perm["model"])
    # 15%: Duplikat mit ANDEREN Rechten (Konflikt)
    if r.random()<0.15:
        dup=os.path.join(sub,fn.replace(".txt","_kopie.txt"))
        perm2=make_perm(r,fn,code)
        wtext(g.p(dup),f"{perm_banner(perm2)}\n\nInhalt: (identisch, andere Rechte!)\n")
        g.add(dup,"share-dup-conflict",perm2["model"])

# =========================================================================== #
#  7) Veraltet / widerrufen (Lineage)                                          #
# =========================================================================== #
def gen_superseded(g, n):
    r=g.r; folder="06_veraltet_widerrufen"
    pool=g.decisions or [f"BESCHLUSS-2022-{i:04d}" for i in range(n)]
    for _ in range(n):
        old=r.choice(pool); nd=rdate(r,date(2024,1,1),date(2025,11,1))
        new=f"BESCHLUSS-{nd.year}-{r.randint(1,9999):04d}"
        st=r.choice(["VERALTET","HINFÄLLIG","WIDERRUFEN","ANGEPASST"])
        reason=r.choice(["Mitarbeiter:in ausgeschieden – Rechte sofort entziehen.",
            "Audit: Rolle zu weitreichend (SoD-Konflikt).","DSGVO-Beanstandung.",
            "Reorg der Fachbereiche.","Befristung abgelaufen."])
        body=(f"[ARCHIV · STATUS: {st}]\n{'='*55}\n"
              f"# Widerruf/Anpassung zu {old}\nNeuer Beschluss: {new} vom {de(nd)}\n"
              f"Status: {st}\nBegründung: {reason}\n"
              f"Wirkung: Rechte aus {old} ab {de(nd)} "
              f"{'widerrufen' if st!='ANGEPASST' else 'angepasst'}.\n"
              f"supersedes: {old}\nsupersededBy: {new}\n")
        rel=os.path.join(folder,safe(f"{de(nd)}_{st}_{old}_{g.nid():04d}")+".md")
        wtext(g.p(rel),body); g.add(rel,"superseded","lineage",st)

# =========================================================================== #
#  GOVERNANCE                                                                  #
# =========================================================================== #
def gen_governance(g):
    r=g.r
    wtext(g.p("00_governance","Berechtigungskonzept_BK-2022-001.md"),
        "# Berechtigungskonzept BK-2022-001\n\nLeast Privilege. Stufen: Öffentlich < Intern "
        "< Vertraulich < Streng vertraulich.\nQuellsysteme nutzen AD, SharePoint, SAP, POSIX, "
        "SQL-GRANT und K8s-RBAC – Zusammenführung über zentrale Matrix.\n")
    g.add("00_governance/Berechtigungskonzept_BK-2022-001.md","governance","Inline")
    matrix={"konzept":"BK-2022-001","zuordnung":[]}
    for name,code in DOMAINS:
        matrix["zuordnung"].append({"bereich":name,"code":code,
            "ad_rw":f"{TENANT}\\GG-{code}-RW","sap_pflege":f"Z_{code}_PFLEGE_1000",
            "sql_role":f"{code.lower()}_rw","k8s_role":f"{code.lower()}-editor"})
    wjson(g.p("00_governance","berechtigungsmatrix_global.json"),matrix)
    g.add("00_governance/berechtigungsmatrix_global.json","governance","mixed")

# =========================================================================== #
#  FESTE TESTANKER (seed-unabhängig)  -> zu den 3 Testfragen                   #
# =========================================================================== #
def plant_anchors(g):
    """Drei deterministische Szenarien mit bekannten Antworten + bekannten Rechten."""
    # ---- ANKER 1: Gehalts-PDF, STRENG VERTRAULICH, nur Geschäftsführung ----
    person_name="Dr. Sabine Vogt"; gehalt="128.500 EUR"
    perm1={"model":"Inline","classification":"STRENG_VERTRAULICH",
           "classification_label":"Streng vertraulich – nur Geschäftsführung (GF)",
           "berechtigte":["Geschäftsführung"]}
    blocks=[(20,">> STRENG VERTRAULICH – NUR GESCHAEFTSFUEHRUNG (GF) <<"),
            (11,f"BERECHTIGUNG: {perm1['classification_label']}\n"
                f"Berechtigte: Geschäftsführung (AD-Gruppe {TENANT}\\GG-GF-ADMIN)\n"),
            (12,""),
            (13,f"{COMPANY} – Gehaltsliste 2025 (Auszug)"),
            (11,f"Mitarbeiter:in: {person_name}\nJahresgehalt 2025: {gehalt}\n"
                f"Kostenstelle: GF-001\nFreigegeben durch: Geschäftsführung\n"),
            (14,"-- STRENG VERTRAULICH – Weitergabe untersagt --")]
    rel1="04_pdf_dokumente/Gehaltsliste_2025__NUR-GF__STRENG-VERTRAULICH.pdf"
    build_pdf(g.p(rel1),blocks); g.add(rel1,"ANKER-pdf","Inline","STRENG_VERTRAULICH")
    wjson(g.p(rel1+".acl.json"),perm1); g.add(rel1+".acl.json","ANKER-acl","Inline","STRENG_VERTRAULICH")

    # ---- ANKER 2: kunden_master.db, Schreibrecht später WIDERRUFEN ----
    rel2="01_datenbanken/Vertrieb/kunden_master.db"
    os.makedirs(os.path.dirname(g.p(rel2)),exist_ok=True)
    con=sqlite3.connect(g.p(rel2)); cur=con.cursor()
    cur.execute("CREATE TABLE kunden(id INTEGER PRIMARY KEY,name TEXT,umsatz REAL,kam TEXT)")
    for i,(nm,u) in enumerate([("ACME AG",182000),("Globex GmbH",94500),("Initech KG",250300)],1):
        cur.execute("INSERT INTO kunden VALUES(?,?,?,?)",(i,nm,u,person(g.r)))
    cur.execute("CREATE TABLE _acl(objekt TEXT,modell TEXT,prinzipal TEXT,recht TEXT,effekt TEXT,geerbt INT)")
    cur.execute("INSERT INTO _acl VALUES(?,?,?,?,?,?)",
                ("kunden_master","ActiveDirectory",f"{TENANT}\\GG-VK-RW","Modify","Allow",0))
    con.commit(); con.close(); g.add(rel2,"ANKER-db","ActiveDirectory")
    # Ursprungs-Beschluss (Gewährung)
    rel2b="03_meetings/2024/2024-01-10_Daten-Governance-Board_Vertrieb_ANKER.md"
    wtext(g.p(rel2b),
        "BERECHTIGUNG: Intern\n\n# Protokoll Daten-Governance-Board – Vertrieb\n"
        "Datum: 10.01.2024\n\n## Beschlüsse\n"
        f"- **BESCHLUSS-2024-0001** [GEWAEHRT]: Schreibzugriff (Modify) auf DB 'kunden_master' "
        f"für AD-Gruppe {TENANT}\\GG-VK-RW.\n")
    g.add(rel2b,"ANKER-meeting","Inline","INTERN")
    # Widerruf
    rel2c="06_veraltet_widerrufen/2025-02-14_WIDERRUFEN_BESCHLUSS-2024-0001_ANKER.md"
    wtext(g.p(rel2c),
        "[ARCHIV · STATUS: WIDERRUFEN]\n=======================================================\n"
        "# Widerruf zu BESCHLUSS-2024-0001\nNeuer Beschluss: BESCHLUSS-2025-0099 vom 14.02.2025\n"
        "Status: WIDERRUFEN\nBegründung: Audit – Schreibrechte des gesamten Vertriebsteams auf "
        "Kunden-Stammdaten zu weitreichend (SoD).\n"
        f"Wirkung: Das in BESCHLUSS-2024-0001 gewährte Schreibrecht von {TENANT}\\GG-VK-RW auf "
        "'kunden_master' ist ab 14.02.2025 WIDERRUFEN. Es verbleibt nur Lesezugriff (GG-VK-RO).\n"
        "supersedes: BESCHLUSS-2024-0001\nsupersededBy: BESCHLUSS-2025-0099\n")
    g.add(rel2c,"ANKER-superseded","lineage","WIDERRUFEN")

    # ---- ANKER 3: umsatz_2025 – SQL-GRANT vs. API-OAuth-Scope (Cross-System) ----
    rel3a="01_datenbanken/Finanzen/umsatz_2025.sql"
    wtext(g.p(rel3a),
        "-- Mustermann Industrie GmbH · Finanzen\n"
        "-- BERECHTIGUNG (SQL): siehe GRANT unten\n"
        "CREATE TABLE umsatz_2025 (monat TEXT PRIMARY KEY, betrag NUMERIC, region TEXT);\n"
        "GRANT SELECT ON umsatz_2025 TO analyst_ro;\n"
        "GRANT SELECT, INSERT, UPDATE ON umsatz_2025 TO fi_rw;\n"
        "REVOKE ALL ON umsatz_2025 FROM fi_praktikant; -- entzogen 2025-03\n")
    g.add(rel3a,"ANKER-sql","SQL_GRANT")
    rel3b="02_api/Finanzen/fi-umsatz-api.openapi.json"
    wjson(g.p(rel3b),{"openapi":"3.0.1","info":{"title":"Finanzen Umsatz API","version":"2.0"},
        "servers":[{"url":f"https://api.{DNS}/fi/v1"}],
        "x-data-source":"db://fi/umsatz_2025",
        "x-permissions":{"model":"OAuth2-Scopes","scopes":{
            "fi:read":"Lesezugriff Umsatz (entspricht DB-Rolle analyst_ro)",
            "fi:write":"Schreibzugriff (entspricht fi_rw)"}},
        "paths":{"/umsatz_2025":{"get":{"security":[{"oauth":["fi:read"]}]}}}})
    g.add(rel3b,"ANKER-api","OAuth2-Scopes")

# =========================================================================== #
#  Manifest + Testfragen                                                       #
# =========================================================================== #
TESTFRAGEN = [
    {"id":"TF1","frage":"Wie hoch ist das Jahresgehalt 2025 von Dr. Sabine Vogt laut Gehaltsliste?",
     "erwartete_antwort":"128.500 EUR.",
     "duerfen_antworten":"NUR Geschäftsführung (AD-Gruppe MUSTERINDUSTRIE\\GG-GF-ADMIN, "
        "Clearance „Streng vertraulich“). Alle anderen: Zugriff verweigern.",
     "quelle":"04_pdf_dokumente/Gehaltsliste_2025__NUR-GF__STRENG-VERTRAULICH.pdf",
     "testet":"PDF-Lesen + Durchsetzung höchster Klassifizierungsstufe."},
    {"id":"TF2","frage":"Hat die AD-Gruppe MUSTERINDUSTRIE\\GG-VK-RW aktuell noch Schreibzugriff "
        "auf die Datenbank kunden_master?",
     "erwartete_antwort":"NEIN. Das per BESCHLUSS-2024-0001 (10.01.2024) gewährte Schreibrecht "
        "wurde durch BESCHLUSS-2025-0099 am 14.02.2025 WIDERRUFEN; es verbleibt nur Lesezugriff "
        "(GG-VK-RO).",
     "duerfen_antworten":"Vertrieb-Leitung/-Admin sowie Geschäftsführung (Berechtigungs-Metadaten, "
        "Clearance ≥ Vertraulich).",
     "quelle":"01_datenbanken/Vertrieb/kunden_master.db + 06_veraltet_widerrufen/...BESCHLUSS-2024-0001...",
     "testet":"Temporale Lineage / Permission-Revocation über mehrere Dateien hinweg."},
    {"id":"TF3","frage":"Welche Datenbank-Rolle darf laut SQL-Skript die Tabelle umsatz_2025 NUR "
        "lesen, und über welchen OAuth-Scope ist dieselbe Tabelle per API erreichbar?",
     "erwartete_antwort":"DB-Rolle analyst_ro (GRANT SELECT) liest nur; per API erreichbar über "
        "den Scope fi:read.",
     "duerfen_antworten":"Finanzen (RO/RW/Admin) und IT/GF. Externe ohne fi-Entitlement: verweigern.",
     "quelle":"01_datenbanken/Finanzen/umsatz_2025.sql + 02_api/Finanzen/fi-umsatz-api.openapi.json",
     "testet":"Cross-System-Mapping: SQL-GRANT-Vokabular <-> OAuth-Scope."},
]

def write_manifest(g):
    buf=io.StringIO(); w=csv.writer(buf,delimiter=";")
    w.writerow(["pfad","typ","berechtigungsmodell","klassifizierung"])
    for row in sorted(g.manifest): w.writerow(row)
    wtext(g.p("_MANIFEST.csv"),buf.getvalue())
    wjson(g.p("TESTFRAGEN.json"),TESTFRAGEN)
    md=["# 3 Testfragen für dein Tool\n"]
    for q in TESTFRAGEN:
        md+=[f"## {q['id']}: {q['frage']}",
             f"- **Erwartete Antwort:** {q['erwartete_antwort']}",
             f"- **Dürfen antworten:** {q['duerfen_antworten']}",
             f"- **Quelle(n):** {q['quelle']}",
             f"- **Testet:** {q['testet']}\n"]
    wtext(g.p("TESTFRAGEN.md"),"\n".join(md))

# =========================================================================== #
#  main                                                                        #
# =========================================================================== #
def main():
    ap=argparse.ArgumentParser(description="Chaos-Korpus-Generator (permission-aware RAG-Test)")
    ap.add_argument("--out",default="./enterprise_chaos")
    ap.add_argument("--total",type=int,default=2500,help="Zieldateien gesamt")
    ap.add_argument("--pdf",type=int,default=0,help="Anzahl Stempel-PDFs (0=auto ~10%%)")
    ap.add_argument("--superseded",type=int,default=80)
    ap.add_argument("--seed",type=int,default=1337)
    args=ap.parse_args()

    r=random.Random(args.seed); os.makedirs(args.out,exist_ok=True)
    g=Gen(args.out,r)
    n_pdf = args.pdf or max(50,int(args.total*0.10))

    gen_governance(g)
    plant_anchors(g)                       # feste Anker zuerst

    db_refs=[]
    for _ in range(max(1,int(args.total*0.20))):
        db_refs.append(gen_db_bundle(g))
        if g.files>=args.total: break
    for _ in range(max(1,int(args.total*0.12))):
        gen_api(g)
        if g.files>=args.total: break
    for _ in range(n_pdf):
        gen_pdf_doc(g)
        if g.files>=args.total: break
    for _ in range(max(1,int(args.total*0.15))):
        gen_share(g,db_refs)
        if g.files>=args.total: break
    for _ in range(max(1,int(args.total*0.12))):
        gen_noise(g)
        if g.files>=args.total: break
    # Rest mit Meetings auffüllen
    guard=0
    while g.files<args.total and guard<args.total*6:
        gen_meeting(g,db_refs); guard+=1
    gen_superseded(g,args.superseded)
    write_manifest(g)

    from collections import Counter
    bk=Counter(m[1] for m in g.manifest); bm=Counter(m[2] for m in g.manifest)
    print(f"\nFertig. Ausgabe: {os.path.abspath(args.out)}")
    print(f"Dateien gesamt: {g.files}  ·  Beschlüsse (Lineage): {len(g.decisions)}")
    print("\nNach Typ (Top 15):")
    for k,v in bk.most_common(15): print(f"  {v:5d}  {k}")
    print("\nNach Berechtigungsmodell:")
    for k,v in bm.most_common(): print(f"  {v:5d}  {k}")
    print("\n"+"="*70+"\n3 TESTFRAGEN (auch in TESTFRAGEN.md / TESTFRAGEN.json):\n"+"="*70)
    for q in TESTFRAGEN:
        print(f"\n[{q['id']}] {q['frage']}")
        print(f"      Erwartet:        {q['erwartete_antwort']}")
        print(f"      Dürfen antworten:{q['duerfen_antworten']}")
        print(f"      Quelle:          {q['quelle']}")

if __name__=="__main__":
    main()
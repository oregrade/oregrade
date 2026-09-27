import subprocess, os, yaml, json
from collections import Counter
from datetime import datetime

def sh(a,cwd): return subprocess.run(a,cwd=cwd,capture_output=True,text=True).stdout

FOUNDATION_ORGS = {"apache":"ASF","prometheus":"CNCF"}
PASS_SURFACE = {"server","daemon","database","object_store","framework_hosted","cli_stateful"}
FLAG_SURFACE = {"desktop_app","cli_stateless"}

def evaluate(a):
    repo=a['repo']; date=f"{a['snapshot']}-12-31"; p="/tmp/r/"+repo.replace("/","__")
    r={"repo":repo,"label":a['label'],"snapshot":a['snapshot']}
    if not os.path.exists(p+"/.git"):
        r["stopped_at"]="clone_unavailable"; return r
    before=f"--before={date}"
    log=sh(["git","log",before,"--pretty=%ae|%ad","--date=short"],p)
    rows=[l.split("|") for l in log.strip().split("\n") if "|" in l]
    if not rows: r["stopped_at"]="no_history_at_date"; return r

    auth=Counter(x[0].lower() for x in rows)
    r["authors_to_date"]=len(auth)
    r["top1_share"]=round(auth.most_common(1)[0][1]/sum(auth.values()),3)
    d=datetime.strptime(date,"%Y-%m-%d")
    since=datetime.fromordinal(d.toordinal()-90).strftime("%Y-%m-%d")
    c90=[l for l in sh(["git","log",before,f"--since={since}","--pretty=%ae"],p).strip().split("\n") if l]
    r["commits_90d"]=len(c90)
    r["days_since_commit"]=(d-datetime.strptime(rows[0][1],"%Y-%m-%d")).days

    rev=sh(["git","rev-list","-1",before,"HEAD"],p).strip()
    tree=[f for f in sh(["git","ls-tree","--name-only",rev],p).split("\n") if f]
    lic=[f for f in tree if os.path.basename(f).upper().startswith(("LICENSE","COPYING"))]
    txt=""
    for f in lic[:3]: txt+=sh(["git","show",f"{rev}:{f}"],p)[:6000].upper()
    if not lic:
        for f in tree:
            if os.path.basename(f).upper().startswith("README"):
                txt+=sh(["git","show",f"{rev}:{f}"],p)[:6000].upper(); break
    spdx="none"
    for n,k in [("AGPL","AFFERO"),("GPL","GNU GENERAL PUBLIC"),("Apache-2.0","APACHE LICENSE"),
                ("MIT","MIT LICENSE"),("MIT","PERMISSION IS HEREBY GRANTED, FREE OF CHARGE"),
                ("BSD","REDISTRIBUTION AND USE IN SOURCE"),("MPL","MOZILLA PUBLIC")]:
        if k in txt: spdx=n; break
    r["license"]=spdx
    contrib=[f for f in tree if "CONTRIBUT" in os.path.basename(f).upper()]
    ctxt="".join(sh(["git","show",f"{rev}:{f}"],p)[:6000].upper() for f in contrib[:2])
    r["cla"]= any(k in ctxt for k in ["CONTRIBUTOR LICENSE AGREEMENT"," CLA ","SIGN THE CLA"])

    # ---- GATE 1 license -------------------------------------------------
    org=repo.split("/")[0].lower()
    if org in FOUNDATION_ORGS or repo=="prometheus/prometheus":
        r["stopped_at"]="gate1_foundation_owned"; return r
    if spdx=="none": r["stopped_at"]="gate1_no_license"; return r
    if spdx in ("AGPL","GPL") and r["authors_to_date"]>25 and not r["cla"]:
        r["stopped_at"]="gate1_copyleft_diffuse"; return r
    # ---- GATE 2 liveness ------------------------------------------------
    if r["commits_90d"]<3 or r["days_since_commit"]>180:
        r["stopped_at"]="gate2_liveness"; return r
    # ---- GATE 3 surface -------------------------------------------------
    if a['surface'] not in PASS_SURFACE|FLAG_SURFACE:
        r["stopped_at"]="gate3_surface"; return r
    r["gate3_flag"]= a['surface'] in FLAG_SURFACE
    # ---- GATE 4 commercialization (form mode) ---------------------------
    if a['entity_at_snapshot']=="funded_company":
        r["stopped_at"]="gate4_existing_company"; return r
    r["gate4_flag"]= a['entity_at_snapshot'] in ("small_consultancy","corporate_parent")
    r["stopped_at"]=None
    return r

ann=yaml.safe_load(open('ann.yaml'))
res=[evaluate(a) for a in ann]
json.dump(res,open('/home/claude/gate_run_001.json','w'),indent=2)

from collections import Counter as C
print("STOP POINT                  n   which labels")
print("-"*72)
for stop,n in C(x['stopped_at'] or 'SURVIVED' for x in res).most_common():
    labs=C(x['label'] for x in res if (x['stopped_at'] or 'SURVIVED')==stop)
    print(f"{str(stop):26} {n:3}   {dict(labs)}")
print("\nSURVIVORS:")
for x in res:
    if x['stopped_at'] is None:
        print(f"  {x['repo']:26} {x['snapshot']}  {x['label']:22} top1={x['top1_share']} flags={x.get('gate3_flag')}/{x.get('gate4_flag')}")

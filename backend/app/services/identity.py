"""身份解析器：按证据等级解析身份簇。

规则：
- 医保伪标识相同 / 有效期内专业凭证 → 强证据，自动成簇；
- 单独共享电话、地址、代办人 → 弱候选，只生成 pending 边，绝不自动合并；
- 人工确认弱候选后按强关联成簇；人工驳回后该证据失效。
每次重算产生不可变 IdentityClusterVersion。
"""
from collections import defaultdict

from sqlalchemy.orm import Session

from ..models import IdentityClusterVersion, IdentityEvidence, Person


def _effective_edges(db: Session, subject_type="person"):
    """返回当前生效的合并边 (left, right)。"""
    edges = []
    evs = db.query(IdentityEvidence).filter_by(subject_type=subject_type).all()
    for ev in evs:
        if ev.status == "rejected":
            continue
        if ev.tier == "strong" and ev.status in ("auto", "confirmed"):
            edges.append((ev.left_code, ev.right_code))
        elif ev.tier == "weak" and ev.status == "confirmed":
            edges.append((ev.left_code, ev.right_code))
    if subject_type == "person":
        # 相同医保伪标识天然强关联（即便漏配证据行也成立）
        pseudo = defaultdict(list)
        for p in db.query(Person).all():
            if p.pseudo_id:
                pseudo[p.pseudo_id].append(p.code)
        for members in pseudo.values():
            for i in range(1, len(members)):
                edges.append((members[0], members[i]))
    return edges


def _union_find(nodes, edges):
    parent = {n: n for n in nodes}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in edges:
        union(a, b)
    groups = defaultdict(list)
    for n in list(parent):
        groups[find(n)].append(n)
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def rebuild_clusters(db: Session, reason: str) -> IdentityClusterVersion:
    persons = [p.code for p in db.query(Person).order_by(Person.code).all()]
    clusters_raw = _union_find(persons, _effective_edges(db, "person"))
    clusters = []
    code_to_cluster = {}
    for i, members in enumerate(clusters_raw, 1):
        cid = f"C{i}"
        clusters.append({"cluster": cid, "members": sorted(members)})
        for m in members:
            code_to_cluster[m] = cid
    version = (db.query(IdentityClusterVersion)
               .order_by(IdentityClusterVersion.version.desc()).first())
    next_ver = 1 if version is None else version.version + 1
    row = IdentityClusterVersion(version=next_ver, reason=reason, clusters=clusters)
    db.add(row)
    db.flush()
    return row


def latest_version(db: Session) -> IdentityClusterVersion:
    return (db.query(IdentityClusterVersion)
            .order_by(IdentityClusterVersion.version.desc()).first())


def cluster_map(db: Session):
    """返回 (version, person→clusterId, clusterId→[persons])。"""
    v = latest_version(db)
    p2c, c2m = {}, {}
    if v:
        for c in v.clusters:
            c2m[c["cluster"]] = c["members"]
            for m in c["members"]:
                p2c[m] = c["cluster"]
    return v, p2c, c2m


def cluster_members_of(db: Session, person_code: str) -> list[str]:
    _, p2c, c2m = cluster_map(db)
    cid = p2c.get(person_code)
    if not cid:
        return [person_code]
    return c2m[cid]

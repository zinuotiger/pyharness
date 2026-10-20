"""Named grants resolve real current account membership, never Agent labels."""
import sqlite3
from contextlib import closing
from pyharness.application.accounts import Accounts,AccountError
from pyharness.errors import raise_code


def recipient_id(service,username):
    path=service._storage_root()/'accounts.sqlite3'
    if username=='local-operator' and service.tenant_id=='default' and not Accounts.manages_tenant(service._storage_root(),'default'):
        return 'local-operator'
    try:name=Accounts.username(username)
    except AccountError:raise_code('EVT-100',reason='delivery_recipient_invalid')
    if not path.is_file():raise_code('GRD-403',reason='delivery_recipient_unavailable')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute("SELECT u.id FROM users u JOIN members m ON m.user_id=u.id WHERE u.username=? AND m.tenant=? AND m.role IN ('owner','member')",(name,service.tenant_id)).fetchone()
    if row is None:raise_code('GRD-403',reason='delivery_recipient_unavailable')
    return row[0]


def require_member(service,subject):
    if subject=='local-operator' and service.tenant_id=='default':return
    path=service._storage_root()/'accounts.sqlite3'
    if not path.is_file():raise_code('GRD-403',reason='delivery_grantor_unavailable')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        found=db.execute("SELECT 1 FROM members WHERE user_id=? AND tenant=? AND role IN ('owner','member')",(subject,service.tenant_id)).fetchone()
    if not found:raise_code('GRD-403',reason='delivery_grantor_unavailable')


def consumer_ceiling(parent,child):
    """A child may narrow capabilities; configuration names confer no permission."""
    from decimal import Decimal
    from pyharness.application.platform_models import AgentDefinition
    p=AgentDefinition.model_validate(parent).model_dump();c=AgentDefinition.model_validate(child).model_dump()
    for key in ('allowed_tools','mcp_connections','skills','knowledge_sources'):
        if not set(c[key]).issubset(p[key]):raise ValueError('consumer capability exceeds parent: '+key)
    for key in ('executor','codex_tools','codex_model','codex_executable'):
        if c[key]!=p[key]:raise ValueError('consumer executor exceeds fixed parent')
    for key in ('memory_scope','permission_policy'):
        if c[key]!=p[key] and c[key] not in ('session','strict'):raise ValueError('consumer policy exceeds parent')
    for key in ('require_evidence','strict_knowledge'):
        if p[key] and not c[key]:raise ValueError('consumer removes required evidence policy')
    if not set(p['approval_rules']).issubset(c['approval_rules']):raise ValueError('consumer removes approvals')
    for key in ('max_rounds','timeout'):
        if c[key]>p[key]:raise ValueError('consumer execution limit exceeds parent')
    for key in p['budget']:
        if Decimal(str(c['budget'][key]))>Decimal(str(p['budget'][key])):raise ValueError('consumer budget exceeds parent')
    pb=p['sandbox_profile'];cb=c['sandbox_profile']
    for key in ('mode','remote_connection','workspace_mode','network','image'):
        if cb[key]!=pb[key]:raise ValueError('consumer sandbox identity exceeds fixed parent')
    for key in ('cpus','memory_mb','pids_limit','timeout','output_limit'):
        if cb[key]>pb[key]:raise ValueError('consumer sandbox resource exceeds parent')
    if c['status']!='published':raise ValueError('consumer must be published')
    return c

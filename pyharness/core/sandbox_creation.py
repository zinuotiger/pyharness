"""Shared sandbox allocation; facade lookups retain existing injection seams."""


def prepare_permissions(api, root, *, prepared):
    if api.os.name == 'nt':return
    for item in [root, *root.rglob('*')]:
        if api.os.getuid() == 0:api.os.chown(item, 65534, 65534)
        if item.is_dir():item.chmod(0o700)
        elif not prepared:item.chmod(0o600)
        # Prepared ordinary files already have their checked captured mode.


def prepare_restored_permissions(api,root):
    """Preserve validated restored modes beneath an always-private owned root."""
    if api.os.name=='nt':return
    api.safe_path(root,'owned-restored-mode-check')
    for item in [root,*root.rglob('*')]:
        if item!=root:api.safe_path(root,item.relative_to(root).as_posix())
        if api.os.getuid()==0:api.os.chown(item,65534,65534)
    root.chmod(0o700)


async def create_owned(manager, run_id, profile, source, preparation, *, authorize=None):
    from pyharness.core import sandbox as api
    if profile.mode == 'disabled':api.fail('sandbox_disabled')
    gate = authorize or (lambda: None)
    async with manager.lock:
        gate()
        if preparation is not None:preparation.verify()
        ident = 'ph-' + api.uuid.uuid4().hex
        record = api.SandboxRecord(sandbox_id=ident, run_id=run_id, backend=profile.mode,
            status='creating', workspace_mode=profile.workspace_mode, cpus=profile.cpus,
            memory_mb=profile.memory_mb, pids_limit=profile.pids_limit,
            network='none' if profile.mode in ('isolated','remote') else 'host_unrestricted')
        root = api.safe_path(manager.root, ident)
        if preparation is not None:preparation.destination(root)
        root.mkdir(parents=True)
        try:
            if preparation is not None:preparation.populate(root)
            elif source is not None and profile.workspace_mode == 'private_copy':api.private_copy(source, root)
            elif profile.workspace_mode == 'private_git_clone':
                if source is None:api.fail('workspace_clone_unavailable')
                await api.private_git_clone(source, root)
            if getattr(preparation,'preserve_directory_modes',False) is True:prepare_restored_permissions(api,root)
            else:prepare_permissions(api, root, prepared=preparation is not None)
            gate()
            if preparation is not None:preparation.verify()
            await manager.backends[profile.mode].create(record, root, profile)
            gate()
            if preparation is not None:preparation.verify()
            record.status = 'running'
        except BaseException as original:
            try:
                if record.status == 'cleanup_failed' or not (isinstance(original, api.PyHError) and original.code == 'sandbox_unavailable'):
                    await api.asyncio.shield(manager.backends[profile.mode].destroy(record))
                # Recheck the exact owned target after the asynchronous backend.
                api.shutil.rmtree(api.safe_path(manager.root, ident))
            except BaseException:
                record.status='cleanup_failed'; record.error_code='sandbox_destroy_failed'
                manager.records[ident]=record
            raise
        manager.records[ident]=record
        return record

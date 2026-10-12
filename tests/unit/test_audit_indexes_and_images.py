import io
import struct
import zlib
from PIL import Image
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.vision import preview_image
from pyharness.errors import PyHError
from tests.support import isolated_settings


async def test_target_outside_global_window_is_persistent_and_never_leaks(tmp_path):
    cfg = isolated_settings(tmp_path)
    app = DesktopApp(cli.assemble_ctx(cfg))
    try:
        for i in range(101):
            sid = await app.service.create_session()
            log = await app.service.require_session(sid)
            await log.append('user.message', {'content': f'elderberry sample {i}'}, actor='user', sync=True)
        rows = (await app.service.list_sessions())['sessions']
        target = rows[100]['sid']
        first = await app.service.platform.history_search('elderberry')
        assert first['truncated'] and all(h['session_id'] != target for h in first['hits'])
        targeted = await app.service.platform.history_search('elderberry', target)
        assert not targeted['truncated'] and len(targeted['hits']) == 1
        assert {h['session_id'] for h in targeted['hits']} == {target}
        assert targeted['indexed_events'] > 0
        again = await app.service.platform.history_search('elderberry')
        assert again['indexed_events'] == 0 and target not in {h['session_id'] for h in again['hits']}
        assert (await app.service.platform.history_search('elderberry', target))['indexed_events'] == 0
        await app.service.shutdown()
        app = DesktopApp(cli.assemble_ctx(cfg))
        restarted = await app.service.platform.history_search('elderberry', target)
        assert restarted['hits'] and not restarted['truncated']
        assert target not in {h['session_id'] for h in (await app.service.platform.history_search('elderberry'))['hits']}
        await app.service.delete_session(target)
        await app.service.platform.history_search('elderberry')
        db = app.service.platform.history_index._db
        assert not db.execute('SELECT 1 FROM events_fts WHERE session_id=?', (target,)).fetchone()
        assert not db.execute('SELECT 1 FROM source_progress WHERE sid=?', (target,)).fetchone()
        with pytest.raises(PyHError):
            await app.service.platform.history_search('elderberry', target)
    finally:
        await app.service.shutdown()


async def test_incremental_history_edit_delete_restart(tmp_path):
    cfg=isolated_settings(tmp_path)
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        sid=await app.service.create_session()
        log=await app.service.require_session(sid)
        event=await log.append('user.message',{'content':'synthetic apricot'},actor='user',sync=True)
        first=await app.service.platform.history_search('apricot')
        assert first['hits'] and first['indexed_events'] > 0
        assert (await app.service.platform.history_search('apricot'))['indexed_events'] == 0
        other=await app.service.create_session()
        other_log=await app.service.require_session(other)
        await other_log.append('user.message',{'content':'second synthetic apricot'},actor='user',sync=True)
        assert (await app.service.platform.history_search('apricot'))['indexed_events'] > 0
        filtered=await app.service.platform.history_search('apricot',sid)
        assert filtered['indexed_events']==0 and len(filtered['hits'])==1
        again=await app.service.platform.history_search('apricot')
        assert again['indexed_events']==0 and len(again['hits'])==2
        await log.append('user.message_edited',{'target_seq':event.seq,'new_content':'synthetic blueberry'},actor='user',sync=True)
        assert not (await app.service.platform.history_search('apricot',sid))['hits']
        assert (await app.service.platform.history_search('blueberry'))['hits']
        await app.service.shutdown()
        app=DesktopApp(cli.assemble_ctx(cfg))
        result=await app.service.platform.history_search('blueberry')
        assert result['hits'] and result['indexed_events'] <= 2
        assert (await app.service.platform.history_search('blueberry',sid))['indexed_events']==0
        assert (await app.service.platform.history_search('apricot'))['indexed_events']==0
        await app.service.delete_session(sid)
        assert not (await app.service.platform.history_search('blueberry'))['hits']
    finally:
        await app.service.shutdown()


@pytest.mark.parametrize('fmt,extension',[('PNG','png'),('JPEG','jpg'),('GIF','gif'),('WEBP','webp')])
def test_complete_image_decoding(fmt,extension):
    stream=io.BytesIO()
    Image.new('RGB',(4,4),'blue').save(stream,format=fmt)
    assert preview_image(stream.getvalue(),'sample.'+extension)['w'] == 4


def test_valid_crc_but_invalid_pixel_data_rejected():
    def chunk(kind,body):
        return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
    raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',4,4,8,2,0,0,0))
    raw+=chunk(b'IDAT',b'invalid compressed pixels')+chunk(b'IEND',b'')
    with pytest.raises(PyHError,match='ATT-001'):
        preview_image(raw,'sample.png')


@pytest.mark.parametrize('frames',[100,101])
def test_animation_frame_work_bound(frames):
    stream=io.BytesIO()
    images=[Image.new('RGB',(2,2),(n,0,0)) for n in range(frames)]
    images[0].save(stream,format='GIF',save_all=True,append_images=images[1:],duration=10,optimize=False)
    if frames==100:
        assert preview_image(stream.getvalue(),'frames.gif')['w']==2
    else:
        with pytest.raises(PyHError,match='ATT-001'):
            preview_image(stream.getvalue(),'frames.gif')


def test_aggregate_animation_pixels_rejected_before_full_decode():
    stream=io.BytesIO()
    a,b=Image.new('P',(4500,4500),1),Image.new('P',(4500,4500),2)
    a.save(stream,format='GIF',save_all=True,append_images=[b],optimize=False)
    with pytest.raises(PyHError,match='ATT-001'):
        preview_image(stream.getvalue(),'aggregate.gif')


def test_declared_pixel_limit_rejected_without_large_allocation():
    def chunk(kind,body):
        return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
    raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',10000,4001,8,2,0,0,0))
    raw+=chunk(b'IDAT',b'not decoded')+chunk(b'IEND',b'')
    with pytest.raises(PyHError,match='ATT-001'):
        preview_image(raw,'oversized.png')

from app.models import ProviderSnapshot
from app.store import Store


def test_store_latest(tmp_path):
    store=Store(str(tmp_path/'x.db'))
    store.save(ProviderSnapshot('a','A','ok','2026-01-01T00:00:00+00:00'))
    store.save(ProviderSnapshot('a','A','error','2026-01-01T00:01:00+00:00'))
    latest=store.latest()
    assert len(latest)==1
    assert latest[0]['status']=='error'

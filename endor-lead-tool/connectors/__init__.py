"""Connector kayıt mekanizması.

Yeni bir connector eklemek için: connectors/ altına dosya oluştur, Connector'ı
uygula, aşağıdaki REGISTRY'ye ekle. Ayarları base.default_settings üzerinden
otomatik bildirilir.
"""
from .base import Connector
from .manual import ManualConnector
from .impressum import ImpressumConnector
from .clutch import ClutchConnector

REGISTRY: dict[str, type[Connector]] = {
    ManualConnector.name: ManualConnector,
    ImpressumConnector.name: ImpressumConnector,
    ClutchConnector.name: ClutchConnector,
}

# Yeni kaynak (Sortlist, DesignRush...) buraya eklenir; mevcut dosyalar değişmez.


def get_connector(name: str, settings: dict | None = None) -> Connector:
    cls = REGISTRY.get(name)
    if not cls:
        raise KeyError(f"Bilinmeyen connector: {name}")
    return cls(settings)


def all_default_settings() -> dict:
    """Ayarlar paneli için her connector'ın varsayılanları."""
    return {name: dict(cls.default_settings) for name, cls in REGISTRY.items()}


def directory_sources() -> list[dict]:
    """'Kaynaklar' menüsü için crawl kaynakları. Yeni connector eklenince
    (is_directory_source=True) otomatik listelenir."""
    return [{"name": name, "label": cls.label or name.title()}
            for name, cls in REGISTRY.items() if cls.is_directory_source]

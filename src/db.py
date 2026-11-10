"""SQLite storage: page cache and listings table (SQLAlchemy 2.x)."""
from datetime import datetime, timedelta

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from src.config import CACHE_TTL_HOURS, DATA_DIR, settings
from src.models import Listing


class Base(DeclarativeBase):
    pass


class PageCache(Base):
    __tablename__ = "page_cache"
    url: Mapped[str] = mapped_column(String, primary_key=True)
    html: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class ListingRow(Base):
    __tablename__ = "listings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    url: Mapped[str] = mapped_column(String)
    property_type: Mapped[str] = mapped_column(String, index=True)
    price_azn: Mapped[int] = mapped_column(Integer)
    rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    area_m2: Mapped[float] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_floors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    district: Mapped[str] = mapped_column(String, index=True)
    metro: Mapped[str | None] = mapped_column(String, nullable=True)
    renovated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_new_building: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime)


_engine = None


def get_engine(url: str | None = None):
    global _engine
    if url is not None:
        return create_engine(url)
    if _engine is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(settings.database_url)
        Base.metadata.create_all(_engine)
    return _engine


def init_db(engine=None) -> None:
    Base.metadata.create_all(engine or get_engine())


# --- page cache ---

def get_cached_page(url: str, engine=None, ttl_hours: int = CACHE_TTL_HOURS) -> str | None:
    with Session(engine or get_engine()) as s:
        row = s.get(PageCache, url)
        if row and datetime.now() - row.fetched_at < timedelta(hours=ttl_hours):
            return row.html
    return None


def put_cached_page(url: str, html: str, engine=None) -> None:
    with Session(engine or get_engine()) as s:
        s.merge(PageCache(url=url, html=html, fetched_at=datetime.now()))
        s.commit()


# --- listings ---

def _to_row(x: Listing) -> ListingRow:
    return ListingRow(**x.model_dump())


def _to_model(r: ListingRow) -> Listing:
    return Listing.model_validate(
        {c.name: getattr(r, c.name) for c in ListingRow.__table__.columns}
    )


def upsert_listings(listings: list[Listing], engine=None) -> int:
    with Session(engine or get_engine()) as s:
        for x in listings:
            s.merge(_to_row(x))
        s.commit()
    return len(listings)


def get_listing(listing_id: str, engine=None) -> Listing | None:
    with Session(engine or get_engine()) as s:
        r = s.get(ListingRow, listing_id)
        return _to_model(r) if r else None


def query_listings(
    property_type: str | None = None,
    districts: list[str] | None = None,
    rooms: int | None = None,
    area_min: float | None = None,
    area_max: float | None = None,
    exclude_id: str | None = None,
    engine=None,
) -> list[Listing]:
    stmt = select(ListingRow)
    if property_type:
        stmt = stmt.where(ListingRow.property_type == property_type)
    if districts:
        stmt = stmt.where(ListingRow.district.in_(districts))
    if rooms is not None:
        stmt = stmt.where(ListingRow.rooms == rooms)
    if area_min is not None:
        stmt = stmt.where(ListingRow.area_m2 >= area_min)
    if area_max is not None:
        stmt = stmt.where(ListingRow.area_m2 <= area_max)
    if exclude_id:
        stmt = stmt.where(ListingRow.id != exclude_id)
    with Session(engine or get_engine()) as s:
        return [_to_model(r) for r in s.scalars(stmt)]


def count_by(engine=None) -> list[tuple[str, str, int]]:
    """(property_type, district, count) rows."""
    stmt = (
        select(ListingRow.property_type, ListingRow.district, func.count())
        .group_by(ListingRow.property_type, ListingRow.district)
        .order_by(ListingRow.property_type, func.count().desc())
    )
    with Session(engine or get_engine()) as s:
        return [tuple(r) for r in s.execute(stmt)]


def last_updated(engine=None) -> datetime | None:
    with Session(engine or get_engine()) as s:
        return s.scalar(select(func.max(ListingRow.scraped_at)))


def list_districts(property_type: str | None = None, engine=None) -> list[str]:
    stmt = select(ListingRow.district).distinct()
    if property_type:
        stmt = stmt.where(ListingRow.property_type == property_type)
    with Session(engine or get_engine()) as s:
        return sorted(s.scalars(stmt))

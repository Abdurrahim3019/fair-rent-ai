from sqlalchemy import create_engine

from scripts.import_csv import export_csv, import_csv
from src import db

CSV = """id,url,property_type,price_azn,rooms,area_m2,floor,total_floors,district,metro,renovated,is_new_building,title
1,https://bina.az/items/1,mənzil,700,2,60,3,9,Nəsimi,Nizami,bəli,1,Test
2,https://bina.az/items/2,obyekt,1500,,80,,,Yasamal,,,,
3,https://bina.az/items/3,qala,1500,,80,,,Yasamal,,,,
"""


def test_import_and_export_roundtrip(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    db.init_db(engine)
    src = tmp_path / "in.csv"
    src.write_text(CSV, encoding="utf-8")
    n, errors = import_csv(str(src), engine=engine)
    assert n == 2
    assert len(errors) == 1 and "sətir 4" in errors[0]
    x = db.get_listing("1", engine=engine)
    assert x.renovated is True and x.is_new_building is True and x.rooms == 2
    assert db.get_listing("2", engine=engine).rooms is None
    out = tmp_path / "out.csv"
    assert export_csv(str(out), engine=engine) == 2
    assert out.read_text(encoding="utf-8").startswith("id,url,property_type")

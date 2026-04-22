from database import Session, Incidencia

def check_ticket_109():
    session = Session()
    try:
        t = session.query(Incidencia).filter_by(id=109).first()
        if t:
            print(f"✅ Ticket 109 FOUND in DB.")
            print(f"   ID: {t.id}")
            print(f"   Usuario: {t.usuario_nombre}")
            print(f"   Estado: {t.estado}")
        else:
            print("❌ Ticket 109 NOT FOUND in DB.")
    finally:
        session.close()

if __name__ == "__main__":
    check_ticket_109()

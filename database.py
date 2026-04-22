from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

Base = declarative_base()

class Incidencia(Base):
    __tablename__ = 'incidencias'
    id = Column(Integer, primary_key=True, autoincrement=True)
    glpi_ticket_id = Column(Integer)  # ID del ticket en GLPI (diferente al ID local)
    fecha_reporte = Column(DateTime, default=datetime.now)
    usuario_nombre = Column(String)
    cedula = Column(String)
    email = Column(String)
    telf = Column(String)
    ubicacion = Column(String)
    unidad = Column(String)
    equipo = Column(String)
    falla = Column(String)
    estado = Column(String, default="Abierto")
    tecnico = Column(String)
    inicio_atencion = Column(DateTime)
    fin_atencion = Column(DateTime)
    satisfaccion = Column(Integer)
    tiempo_percibido = Column(String)
    user_id = Column(String)
    group_message_id = Column(Integer)  # ID del mensaje en el grupo de admins
    has_photo = Column(Integer, default=0)  # 1 si el mensaje tiene foto/video

engine = create_engine('sqlite:///soporte_tecnico.db')
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
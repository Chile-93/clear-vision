import os
import urllib
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_DRIVER = os.getenv("DB_DRIVER")


odbc_str = (
  f"DRIVER={{{DB_DRIVER}}};"
   f"SERVER={DB_HOST};"
   f"DATABASE={DB_NAME};"
   "Trusted_Connection=yes;"
)
params = urllib.parse.quote_plus(odbc_str)

engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}", pool_pre_ping=True)

def get_engine():
    return engine


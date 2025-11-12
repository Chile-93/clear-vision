import os
from dotenv import load_dotenv
from sqlalchemy import create_engine

# Load environment variables from .env
load_dotenv()

# Get the Render PostgreSQL connection string
DB_URL = os.getenv("DB_URL")

# Create SQLAlchemy engine
engine = create_engine(DB_URL, pool_pre_ping=True)

def get_engine():
    return engine



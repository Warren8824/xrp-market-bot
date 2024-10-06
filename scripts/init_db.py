from sqlalchemy import create_engine, text, inspect, func
from sqlalchemy.exc import OperationalError
import psycopg2
from psycopg2 import sql

import sys

import path_setup  # Needed to access src folder
from src.utils.logger import scripts_logger
from src.utils.config import config
from src.models import get_models

# Setup model instances
models = get_models()
Base = models["Base"]
MarketData15Min = models["MarketData15Min"]
OHLCVData15Min = models["OHLCVData15Min"]
TechnicalIndicators15Min = models["TechnicalIndicators15Min"]

# Define global table information
TABLES = [
    {"name": "market_data_15_min", "model": MarketData15Min, "timestamp_column": "timestamp"},
    {"name": "ohlcv_data_15_min", "model": OHLCVData15Min, "timestamp_column": "timestamp"},
    {"name": "technical_indicators_15_min", "model": TechnicalIndicators15Min, "timestamp_column": "timestamp"},
]


def create_database_if_not_exists(db_url):
    db_name = db_url.split("/")[-1]
    conn = psycopg2.connect(
        host=config["database"]["host"],
        user=config["database"]["user"],
        password=config["database"]["password"],
        dbname="postgres",
    )
    conn.autocommit = True
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
    if cursor.fetchone() is None:
        scripts_logger.info(f"Database {db_name} does not exist, creating it...")
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
        scripts_logger.info(f"Database {db_name} created successfully.")
    else:
        scripts_logger.info(f"Database {db_name} already exists.")

    cursor.close()
    conn.close()


def check_tables_exist(inspector):
    required_tables = set(table["name"] for table in TABLES)
    existing_tables = set(inspector.get_table_names())
    return required_tables.issubset(existing_tables)


def fetch_table_date_range(engine, table_name, timestamp_column):
    with engine.connect() as conn:
        query = f"""
            SELECT MIN({timestamp_column}), MAX({timestamp_column})
            FROM {table_name}
        """
        result = conn.execute(text(query)).fetchone()
        return result[0], result[1]


def drop_all_data(engine):
    with engine.connect() as conn:
        for table in TABLES:
            table_name = table["name"]
            row_count_before = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
            scripts_logger.info(f"Table {table_name} has {row_count_before} rows before deletion.")

            truncate_command = f"TRUNCATE TABLE {table_name} CASCADE"
            scripts_logger.info(f"Executing command: {truncate_command}")
            conn.execute(text(truncate_command))
            conn.commit()

            row_count_after = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
            scripts_logger.info(f"Table {table_name} has {row_count_after} rows after deletion.")

            if row_count_after != 0:
                scripts_logger.warning(f"Warning: Data in {table_name} was not deleted as expected.")


def prompt_user_for_action(engine):
    with engine.connect() as connection:
        for table in TABLES:
            table_name = table["name"]
            row_count = connection.execute(text(f"SELECT COUNT(*) FROM {table_name};")).scalar()

            if row_count > 0:
                user_input = input(f"Table {table_name} has {row_count} rows. Do you want to delete all data from this table and begin fresh? (yes/no): ").strip().lower()

                if user_input == 'yes':
                    connection.execute(text(f"TRUNCATE TABLE {table_name} CASCADE;"))
                    connection.commit() # Complete the truncation of the data
                    scripts_logger.info(f"All data from {table_name} has been truncated.")
                elif user_input == 'no':
                    scripts_logger.info(f"Continuing with the existing data in {table_name}.")
                else:
                    print("Invalid input. Please enter 'yes' or 'no'.")
                    return prompt_user_for_action(engine)  # Recursively prompt again
            else:
                scripts_logger.info(f"Table {table_name} has no data.")

    print("No more tables to process.")


def init_db():
    try:
        db_url = config["database"]["url"]
        create_database_if_not_exists(db_url)
        engine = create_engine(db_url)
        scripts_logger.info("Attempting to connect to the database.")

        with engine.connect():
            scripts_logger.info("Successfully connected to the database.")

        inspector = inspect(engine)

        if check_tables_exist(inspector):
            print("All required tables already exist.")
            prompt_user_for_action(engine)
        else:
            scripts_logger.info("Initializing the database...")

            with engine.connect() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
                scripts_logger.info("TimescaleDB extension created successfully.")

            Base.metadata.create_all(engine)
            scripts_logger.info("All required tables created successfully.")

            with engine.connect() as conn:
                for table in TABLES:
                    table_name = table["name"]
                    time_column = table["timestamp_column"]
                    if table_name in inspector.get_table_names():
                        try:
                            conn.execute(
                                text(
                                    f"SELECT create_hypertable('{table_name}', '{time_column}', if_not_exists => TRUE, chunk_time_interval => INTERVAL '1 hour', migrate_data => TRUE)"
                                )
                            )
                            scripts_logger.info(f"Created hypertable for {table_name}")
                        except Exception as e:
                            scripts_logger.error(
                                f"Error creating hypertable for {table_name}: {str(e)}",
                                exc_info=True,
                            )
                    else:
                        scripts_logger.warning(f"Table {table_name} does not exist (should have been created)")

    except OperationalError as e:
        scripts_logger.error(f"Database connection error: {str(e)}")
        raise
    except Exception as e:
        scripts_logger.error(f"An unexpected error occurred: {str(e)}")
        raise


def main():
    scripts_logger.info("Starting database initialization script.")
    try:
        init_db()
        scripts_logger.info("Database initialization script finished.")
    except Exception as e:
        scripts_logger.error(f"Error during database initialization: {e}", exc_info=True)


if __name__ == "__main__":
    main()

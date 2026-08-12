import psycopg2

conn = psycopg2.connect(
    host="localhost",
    database="practice",
    user="postgres",
    password="your_password",
    port="5432"
)
cur = conn.cursor()

# Simulate malicious "user input"
user_input = "NVDA'; DROP TABLE injection_demo; --"

# UNSAFE: building SQL by gluing strings together
query = f"SELECT * FROM injection_demo WHERE symbol = '{user_input}'"
print("Actual SQL sent to Postgres:")
print(query)

cur.execute(query)
conn.commit()

print("Query ran without error.")

cur.close()
conn.close()
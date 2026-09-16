1. User asks a question in a natural language \#PROMPT

2. SQL Agent gets all the table details (along with description) using “Describe All Tables Tool” and checks with LLM as which all tables relevant for the given user query \#TOOL \#LLM

3. SQL Agent gets all the schema (column) details & description for the selected tables using “Get Schema & Data Tool” along with few data samples (3–5 rows) from each table and asks LLM to generate a SQL based on the supplied data \#TOOL \#LLM

4. SQL Agent validates the SQL with the help of “SQL Query Validator Tool” that in turns takes help of LLM to validate the query based on the given dialect. If there are errors, then Agent again asks LLM to regenerate the query \#TOOL \#LLM

5. SQL Agent executes the query using “Execute Query Tool” to get structured output \#TOOL

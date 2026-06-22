import asyncio
import aiosqlite
import json
from litellm import acompletion
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AetherisMemory")

class SessionMemory:
    """
    Handles SQLite-based long-term memory for an Agentic session.
    Includes an automatic compaction hook for M1 optimization.
    """
    def __init__(self, db_path: str = "memory.db", session_id: str = "default"):
        self.db_path = db_path
        self.session_id = session_id
        self.token_threshold = 6000  # Threshold to trigger compaction

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await db.commit()

    async def add_message(self, role: str, content: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (self.session_id, role, content)
            )
            await db.commit()
            
        await self._check_compaction()

    async def get_history(self) -> list:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
                (self.session_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [{"role": row[0], "content": row[1]} for row in rows]

    async def _check_compaction(self):
        """
        If memory gets too large, compress it into a summary to save M1 RAM and context window.
        """
        history = await self.get_history()
        # Very rough heuristic: 1 word ~ 1.3 tokens
        approx_tokens = sum(len(str(m["content"]).split()) * 1.3 for m in history)
        
        if approx_tokens > self.token_threshold:
            logger.info(f"Memory threshold exceeded ({approx_tokens} > {self.token_threshold}). Triggering Compaction...")
            await self._compact_memory(history)

    async def _compact_memory(self, history: list):
        """
        Uses an LLM (ideally a cheap/local one) to summarize the history.
        """
        try:
            # We compress everything except the last 3 messages to keep immediate context
            to_compress = history[:-3]
            logger.info("Sending history to Ollama for summarization...")
            response = await acompletion(
                model="ollama/llama3",
                messages=[{"role": "system", "content": prompt}],
                max_tokens=500
            )
            
            summary = response.choices[0].message.content
            
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM messages WHERE session_id = ? AND role != 'system'", (self.session_id,))
                
                summary_text = f"[COMPACTED HISTORY SUMMARY]\n{summary}"
                await db.execute(
                    "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                    (self.session_id, "system", summary_text)
                )
                
                for msg in recent:
                    await db.execute(
                        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                        (self.session_id, msg["role"], msg["content"])
                    )
                await db.commit()
            
            logger.info("Compaction successful.")
            
        except Exception as e:
            logger.warning(f"Compaction failed (maybe Ollama is not running?). Error: {e}")
            logger.info("Falling back to simple truncation...")
            # Fallback: keep only the last 10 messages
            recent = history[-10:]
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM messages WHERE session_id = ?", (self.session_id,))
                summary_text = f"[AUTO-TRUNCATED] Conversation history reduced to last {len(recent)} messages due to compaction failure."
                await db.execute(
                    "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                    (self.session_id, "system", summary_text)
                )
                for msg in recent:
                    await db.execute(
                        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                        (self.session_id, msg["role"], msg["content"])
                    )
                await db.commit()

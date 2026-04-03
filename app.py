import streamlit as st
import ollama
from pymongo import MongoClient
import uuid
from datetime import datetime, timezone
import os
import re
from dotenv import load_dotenv

# --- LOAD ENVIRONMENT VARIABLES ---
load_dotenv()

MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASS = os.getenv("MONGO_PASS")
MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = os.getenv("MONGO_PORT", "27017")
DB_NAME = os.getenv("MONGO_DB", "local_ai_db")

MONGO_URI = f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/"
MODEL_NAME = "qwen3.5:4b"
COLLECTION_NAME = "chat_sessions"

# --- DATABASE SETUP ---
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]
except Exception as e:
    st.error(f"Database Connection Error: {e}")
    st.stop()

st.set_page_config(page_title="Local AI Chat Pro", layout="wide")

# --- SESSION STATE MANAGEMENT ---
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "current_title" not in st.session_state:
    st.session_state.current_title = "New Chat"
if "delete_mode_sid" not in st.session_state:
    st.session_state.delete_mode_sid = None
if "edit_mode_sid" not in st.session_state:
    st.session_state.edit_mode_sid = None


# --- CORE FUNCTIONS ---
def save_message(session_id, role, content):
    collection.update_one(
        {"session_id": session_id},
        {
            "$push": {"messages": {"role": role, "content": content}},
            "$setOnInsert": {
                "created_at": datetime.now(timezone.utc),
                "title": st.session_state.current_title,
            },
        },
        upsert=True,
    )


def delete_chat(session_id):
    collection.delete_one({"session_id": session_id})
    if st.session_state.session_id == session_id:
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.current_title = "New Chat"
    st.session_state.delete_mode_sid = None
    st.rerun()


def rename_chat(session_id, new_name):
    if new_name.strip():
        collection.update_one(
            {"session_id": session_id}, {"$set": {"title": new_name.strip()}}
        )
        if st.session_state.session_id == session_id:
            st.session_state.current_title = new_name.strip()
    st.session_state.edit_mode_sid = None
    st.rerun()


def update_session_title(session_id, user_input):
    """Bulletproof title generation for CPU-based local LLMs."""
    try:
        # STEP 1: Manually strip instructions from the user input first
        # This helps the CPU-based LLM focus only on the subject
        clean_input = user_input.lower()
        for word in [
            "explain",
            "short",
            "sentences",
            "in",
            "2-3",
            "what is",
            "tell me",
            "about",
            "?",
        ]:
            clean_input = clean_input.replace(word, "")

        # STEP 2: Strict "Filing Clerk" Prompt
        t_prompt = (
            f"Context: {clean_input.strip()}\n"
            f"Task: Provide a 2-word category name for the context above.\n"
            f"Constraint: No sentences. No punctuation. No verbs.\n"
            f"Category Name:"
        )

        res = ollama.generate(
            model=MODEL_NAME,
            prompt=t_prompt,
            options={
                "num_predict": 10,
                "temperature": 0.0,  # Zero randomness for maximum strictness
                "stop": ["\n", ".", "User", "Assistant"],
            },
        )

        raw_res = (
            res["response"].strip().replace('"', "").replace("Category:", "").strip()
        )

        # STEP 3: Sanitize and Format
        # Remove any non-alphabetic characters (like "2-3" or "?")
        clean_res = re.sub(r"[^a-zA-Z\s]", "", raw_res)
        words = clean_res.split()

        if len(words) > 0:
            # Take up to 3 words and convert to Title Case
            new_title = " ".join(words[:3]).title()
        else:
            # Fallback to the stripped user input if LLM is silent
            new_title = clean_input.strip()[:20].title()

        # Update Database and State
        collection.update_one(
            {"session_id": session_id}, {"$set": {"title": new_title}}
        )
        st.session_state.current_title = new_title

    except Exception:
        # Final emergency fallback to first few words of input
        fallback = user_input.split()[:2]
        collection.update_one(
            {"session_id": session_id}, {"$set": {"title": " ".join(fallback).title()}}
        )


# --- SIDEBAR: CHAT HISTORY ---
with st.sidebar:
    st.title("🤖 Local History")
    if st.button("➕ New Chat", use_container_width=True):
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.current_title = "New Chat"
        st.session_state.edit_mode_sid = None
        st.session_state.delete_mode_sid = None
        st.rerun()

    st.divider()

    sessions = list(collection.find().sort("created_at", -1).limit(15))

    for s in sessions:
        sid = s["session_id"]
        title = s.get("title", "Untitled")

        # --- UI LOGIC FOR EACH ROW ---

        # 1. EDIT MODE: Show Input + Save/Cancel
        if st.session_state.edit_mode_sid == sid:
            new_title_input = st.text_input(
                "New Name", value=title, key=f"in_{sid}", label_visibility="collapsed"
            )
            ec1, ec2 = st.columns(2)
            if ec1.button("✅", key=f"save_{sid}", help="Save"):
                rename_chat(sid, new_title_input)
            if ec2.button("❌", key=f"can_e_{sid}", help="Cancel"):
                st.session_state.edit_mode_sid = None
                st.rerun()

        # 2. DELETE MODE: Show Confirm/Cancel
        elif st.session_state.delete_mode_sid == sid:
            st.warning(f"Delete '{title}'?")
            dc1, dc2 = st.columns(2)
            if dc1.button("✔️", key=f"conf_{sid}", help="Confirm Delete"):
                delete_chat(sid)
            if dc2.button("❌", key=f"can_d_{sid}", help="Cancel"):
                st.session_state.delete_mode_sid = None
                st.rerun()

        # 3. NORMAL MODE: Show Title + Edit + Bin
        else:
            col1, col2, col3 = st.columns([0.6, 0.2, 0.2])
            with col1:
                if st.button(title, key=f"btn_{sid}", use_container_width=True):
                    st.session_state.session_id = sid
                    st.session_state.current_title = title
                    st.session_state.edit_mode_sid = None
                    st.session_state.delete_mode_sid = None
                    st.rerun()
            with col2:
                if st.button("✏️", key=f"ed_{sid}", help="Edit Title"):
                    st.session_state.edit_mode_sid = sid
                    st.session_state.delete_mode_sid = None  # Close delete if open
                    st.rerun()
            with col3:
                if st.button("🗑️", key=f"del_{sid}", help="Delete Chat"):
                    st.session_state.delete_mode_sid = sid
                    st.session_state.edit_mode_sid = None  # Close edit if open
                    st.rerun()

# --- MAIN CHAT INTERFACE ---
st.header(f"📍 {st.session_state.current_title}")

current_doc = collection.find_one({"session_id": st.session_state.session_id})
chat_history = current_doc["messages"] if current_doc else []

for msg in chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Message your local AI..."):
    with st.chat_message("user"):
        st.markdown(prompt)
    save_message(st.session_state.session_id, "user", prompt)

    with st.chat_message("assistant"):
        res_box = st.empty()
        full_res = ""

        # CPU Optimization: Keep context small
        ctx = chat_history[-3:] if len(chat_history) > 3 else chat_history
        msgs = [{"role": m["role"], "content": m["content"]} for m in ctx]
        msgs.append({"role": "user", "content": prompt})

        with st.status("Thinking...", expanded=False):
            stream = ollama.chat(
                model=MODEL_NAME, messages=msgs, stream=True, options={"num_thread": 4}
            )
            for chunk in stream:
                full_res += chunk["message"]["content"]
                res_box.markdown(full_res + "▌")

        res_box.markdown(full_res)

    save_message(st.session_state.session_id, "assistant", full_res)

    # Initial title generation
    if not chat_history:
        update_session_title(st.session_state.session_id, prompt)
        st.rerun()

# EasyCanvas Project Context

## Project

EasyCanvas is a web-based academic planning application for university students.

It connects to Canvas LMS and retrieves real academic information such as:
- courses
- assignments
- due dates
- points
- submission status

EasyCanvas helps students understand what coursework deserves attention first and eventually creates explainable study plans.

## Main Product Idea

Canvas already shows assignments and deadlines.

EasyCanvas should not simply recreate the Canvas assignment list.

The main value is:
1. Retrieve real Canvas data
2. Prioritize unfinished coursework
3. Explain why something has higher priority
4. Eventually allow natural-language academic planning

## Current Development Stage

We are currently building the foundation.

Do not implement MCP or LangGraph yet.

Current priorities:

1. Canvas API integration
2. FastAPI backend
3. Next.js frontend
4. Display real Canvas courses and assignments
5. Basic deterministic priority engine
6. Automated tests

After the core system works:
- LLM integration
- MCP tools
- LangGraph agent workflow

## Tech Stack

Frontend:
- Next.js
- TypeScript
- Tailwind CSS

Backend:
- Python
- FastAPI

Data:
- Canvas LMS API
- PostgreSQL later

AI later:
- LLM
- MCP
- LangGraph

## Current Architecture

Student
↓
Next.js frontend
↓
FastAPI backend
↓
Canvas Service
↓
Canvas API

The FastAPI backend will also contain a deterministic priority engine.

## Canvas Authentication

For development, use an authorized personal Canvas access token.

The token must:
- stay in .env
- never appear in source code
- never be committed to GitHub
- never be exposed to the frontend

For a real multi-user production release, the intended architecture is Canvas OAuth.

Do not build the application around asking users to paste Canvas tokens.

## AI and Hallucination Design

Canvas API is the source of truth for:
- courses
- assignments
- deadlines
- points
- submission status

The priority engine will use deterministic Python logic.

The LLM will later be used for:
- conversation
- explanations
- study planning

The LLM must not invent assignments or deadlines.

## Priority Engine

Priority may eventually consider:
- deadline urgency
- submission status
- grade importance
- estimated effort
- available study time

The exact formula is not finalized.

It must be transparent and testable.

## Development Principles

- Build the simplest working MVP first.
- Keep frontend and backend separate.
- Keep Canvas API logic in its own service.
- Keep priority calculation separate from Canvas extraction.
- Use reusable React components.
- Write tests for important backend logic.
- Never expose secrets.
- Do not add MCP or LangGraph until the core application works.

## Target Repository Structure

EasyCanvas/
├── frontend/
├── backend/
│   └── app/
│       ├── main.py
│       ├── canvas_service.py
│       ├── models.py
│       └── priority.py
├── AGENTS.md
├── .gitignore
└── README.md
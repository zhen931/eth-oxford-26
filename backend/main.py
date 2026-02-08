import os
import json
import asyncio
import httpx
from dotenv import load_dotenv
from typing import TypedDict, List, Optional

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END

# --- CONFIGURATION ---
MODE = "DEMO"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Initialize Model
llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0.3, api_key=GROQ_API_KEY)

# Initialize Geocoder
geolocator = Nominatim(user_agent="aegis-disaster-relief")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- GLOBAL STATE ---
GLOBAL_DISASTERS = []

# --- DATA MODELS ---
class AidRequest(BaseModel):
    disaster_id: str
    description: str
    lat: float
    lng: float
    aid_type: Optional[str] = None

# --- LANGGRAPH SETUP ---
class AgentState(TypedDict):
    messages: List[str]
    context: str
    user_request: str
    iteration: int
    verdict: str

def agent_node(state: AgentState, name: str, style: str):
    prompt = (
        f"You are {name}. Style: {style}. Situation: {state['context']}. "
        f"Request: {state['user_request']}. Debate history: {state['messages']}. "
        "Provide a 15-word response challenging or supporting the request based on your persona."
    )
    res = llm.invoke(prompt)
    return {"messages": [f"{name}: {res.content}"], "iteration": state['iteration'] + 1}

def judge_node(state: AgentState):
    prompt = (
        f"You are the final judge reviewing an aid request debate.\n"
        f"Agent messages: {state['messages']}\n\n"
        f"Rules:\n"
        f"- If the majority of agents support the request, respond VALID\n"
        f"- If agents partially agree but want changes, respond MODIFIED\n"
        f"- If the majority of agents oppose or doubt the request, respond DECLINED\n\n"
        f"Respond with exactly one word: VALID, MODIFIED, or DECLINED."
    )
    res = llm.invoke(prompt)
    raw = res.content.strip().upper().replace(".", "")
    # Ensure we only return a valid verdict
    if "DECLINED" in raw:
        verdict = "DECLINED"
    elif "MODIFIED" in raw:
        verdict = "MODIFIED"
    else:
        verdict = "VALID"
    return {"verdict": verdict}

workflow = StateGraph(AgentState)
workflow.add_node("Miller", lambda s: agent_node(s, "The Skeptic (Miller)", "Skeptical Auditor"))
workflow.add_node("Aris", lambda s: agent_node(s, "The Empath (Dr. Aris)", "Clinical Lead"))
workflow.add_node("Judge", judge_node)

workflow.set_entry_point("Miller")
workflow.add_edge("Miller", "Aris")
workflow.add_conditional_edges("Aris", lambda s: "Judge" if s["iteration"] >= 4 else "Miller")
workflow.add_edge("Judge", END)
graph = workflow.compile()

# --- BACKGROUND TASKS ---
async def fetch_real_time_disasters():
    global GLOBAL_DISASTERS
    while True:
        new_events = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson")
                if res.status_code == 200:
                    data = res.json()
                    for f in data.get('features', [])[:10]:
                        new_events.append({
                            "id": f["id"],
                            "name": f"Quake: {f['properties']['place']}",
                            "lat": f["geometry"]["coordinates"][1],
                            "lon": f["geometry"]["coordinates"][0],
                            "radius": 100
                        })
        except Exception as e:
            print(f"Scraper Error: {e}")

        if not new_events:
            new_events = [
                {"id": "d1", "name": "Valencia Flood", "lat": 39.4699, "lon": -0.3763, "radius": 30},
                {"id": "d2", "name": "California Wildfire", "lat": 34.0522, "lon": -118.2437, "radius": 50},
                {"id": "d3", "name": "Oxford Flash Flood", "lat": 51.7534, "lon": -1.2540, "radius": 100},
            ]

        GLOBAL_DISASTERS = new_events
        await asyncio.sleep(300)

@app.on_event("startup")
async def startup():
    asyncio.create_task(fetch_real_time_disasters())

# --- ENDPOINTS ---

@app.get("/disasters")
async def get_disasters():
    return GLOBAL_DISASTERS

@app.get("/nearby")
async def check_nearby(lat: float = Query(...), lng: float = Query(...)):
    """Check if user is near any known disaster. Returns closest threat or safe status."""
    if MODE == "DEMO":
        demo_event = {
            "id": "demo-001",
            "name": "SIMULATED: Urban Emergency",
            "lat": lat,
            "lon": lng,
            "radius": 10
        }
        return {
            "safe": False,
            "disaster": {**demo_event, "distance_km": 0.0},
            "distance_km": 0.0,
            "location_name": "Demo Environment (Simulated)",
        }

    closest = None
    closest_distance = float("inf")
    for disaster in GLOBAL_DISASTERS:
        try:
            distance = geodesic((lat, lng), (disaster["lat"], disaster["lon"])).km
            if distance <= disaster["radius"] and distance < closest_distance:
                closest = disaster
                closest_distance = distance
        except Exception:
            continue

    location_name = "Unknown Location"
    try:
        location = geolocator.reverse(f"{lat}, {lng}", exactly_one=True, language="en")
        if location:
            addr = location.raw.get("address", {})
            city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county", "")
            country = addr.get("country", "")
            location_name = f"{city}, {country}" if city else country
    except Exception:
        pass

    if closest:
        return {
            "safe": False,
            "disaster": {**closest, "distance_km": round(closest_distance, 2)},
            "distance_km": round(closest_distance, 2),
            "location_name": location_name,
        }

    return {"safe": True, "location_name": location_name}

@app.post("/evaluate")
async def evaluate_aid(req: AidRequest):
    # Check if it's our Demo disaster
    if MODE == "DEMO" and req.disaster_id == "demo-001":
        disaster = {"name": "Simulated Urban Emergency", "lat": req.lat, "lon": req.lng, "radius": 10}
        distance = 0.0
    else:
        disaster = next((d for d in GLOBAL_DISASTERS if d["id"] == req.disaster_id), None)
        if not disaster:
            disaster = {"name": "Manual Override Event", "lat": req.lat, "lon": req.lng, "radius": 50}
        distance = geodesic((req.lat, req.lng), (disaster["lat"], disaster["lon"])).km

    if distance > disaster["radius"] and MODE != "DEMO":
        return {
            "status": "DECLINED",
            "reason": f"User is {round(distance)}km away. Outside zone."
        }

    # AI Debate
    aid_type = req.aid_type or req.description.split(".")[0][:50]
    initial_state = {
        "messages": [],
        "context": f"Disaster: {disaster['name']}. Aid Type: {aid_type}",
        "user_request": req.description,
        "iteration": 0,
        "verdict": ""
    }

    final_state = await graph.ainvoke(initial_state)
    transcript = final_state.get("messages", [])
    verdict = final_state.get("verdict", "PENDING")
    transcript.append(f"The Arbiter: VERDICT IS {verdict}")

    return {
        "status": "PROCESSED",
        "distance_km": round(distance, 2),
        "debate": transcript,
        "final_verdict": verdict
    }

@app.get("/evaluate-stream")
async def evaluate_stream(request_text: str, context: str):
    """SSE endpoint that streams each agent's response in real-time."""
    async def stream():
        state = {"messages": [], "context": context, "user_request": request_text, "iteration": 0, "verdict": ""}
        async for event in graph.astream(state):
            for node, output in event.items():
                if "messages" in output:
                    yield f"data: {json.dumps({'type': 'comment', 'text': output['messages'][-1]})}\n\n"
                if "verdict" in output:
                    yield f"data: {json.dumps({'type': 'verdict', 'text': output['verdict']})}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
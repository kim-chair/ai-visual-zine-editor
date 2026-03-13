flowchart LR
  classDef user fill:#24384f,stroke:#24384f,stroke-width:2px,color:#fff;
  classDef frontend fill:#6c63ff,stroke:#6c63ff,stroke-width:2px,color:#fff;
  classDef processing fill:#2d8fd5,stroke:#2d8fd5,stroke-width:2px,color:#fff;
  classDef ai fill:#27ae60,stroke:#27ae60,stroke-width:2px,color:#fff;
  classDef output fill:#d35400,stroke:#d35400,stroke-width:2px,color:#fff;
  classDef ext fill:#f2f2f2,stroke:#999,stroke-width:1px,color:#111;
  classDef note fill:#fffbea,stroke:#d4b106,stroke-width:1px,color:#111;

  U((User / Judge)):::user
  Y["Public YouTube URL"]:::ext
  R["Uploaded Reference Images"]:::ext

  subgraph APP["Application Layer (Local Streamlit App)"]
    direction LR
    UI["Streamlit Web App UI"]:::frontend
    ORCH["Python Orchestrator"]:::frontend

    subgraph LOCAL["Local Media + Layout Pipeline"]
      direction TB
      DL["yt-dlp<br/>Metadata + Video Download"]:::processing
      CV["OpenCV<br/>Candidate Frame Extraction + Quality Scoring"]:::processing
      ASSET["Uploaded Image Normalization<br/>+ Asset Handling"]:::processing
      EXP["HTML / PDF / Markdown Export"]:::output
    end
  end

  subgraph GC["Google Cloud"]
    direction TB
    SDK["Google Gen AI SDK (`google-genai`)<br/>on Vertex AI"]:::note

    subgraph VA["Gemini + Media Models"]
      direction TB
      G1["Gemini 3.1 Pro Preview<br/>Source Video Analysis"]:::ai
      G2["Gemini 3.1 Pro Preview<br/>Editorial Frame Selection"]:::ai
      G3["Gemini 3.1 Pro Preview<br/>Editorial Conversation"]:::ai
      G4["Gemini 3.1 Pro Preview<br/>Final Issue Publisher"]:::ai
      GI["Gemini Flash Image<br/>Backdrop Generation"]:::ai
      LY["Lyria 2<br/>Optional Soundtrack Generation"]:::ai
    end
  end

  U --> UI
  Y --> UI
  R --> UI

  UI --> ORCH
  ORCH --> DL
  DL --> CV
  ORCH --> ASSET

  DL --> G1
  CV --> G2

  G1 --> ORCH
  G2 --> ORCH

  ORCH --> G3
  ASSET --> G3
  G1 --> G3
  G2 --> G3

  ORCH --> G4
  ASSET --> G4
  G1 --> G4
  G2 --> G4
  G3 --> G4

  SDK -. enables .-> G1
  SDK -. enables .-> G2
  SDK -. enables .-> G3
  SDK -. enables .-> G4
  SDK -. enables .-> GI
  SDK -. enables .-> LY

  G4 --> GI
  G4 --> LY

  ORCH --> EXP
  G4 --> EXP
  GI --> EXP
  LY --> EXP

  EXP --> UI
  UI --> U

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
  R["Uploaded Reference Images<br/>(chat attachments)"]:::ext

  subgraph APP["Application Layer (Local Streamlit App)"]
    direction LR
    UI["Streamlit Web App UI"]:::frontend
    ORCH["Python Orchestrator<br/>(single-file handlers + session state)"]:::frontend

    subgraph LOCAL["Local Media / Asset / Export Pipeline"]
      direction TB
      META["yt-dlp<br/>Metadata Only"]:::processing
      DL["yt-dlp<br/>Video Download"]:::processing
      CV["OpenCV<br/>Candidate Frame Extraction<br/>+ Quality Scoring"]:::processing
      SF["Selected Editorial<br/>Frame Set"]:::processing
      ASSET["Uploaded Image Normalization<br/>+ Asset Collection"]:::processing
      MD["Markdown Export Builder"]:::output
      HTML["HTML Layout Builder"]:::output
      PDF["PDF Render<br/>(WeasyPrint)"]:::output
    end
  end

  subgraph GC["Google Cloud / Vertex AI"]
    direction TB
    SDK["Google Gen AI SDK (google-genai)<br/>Vertex AI client"]:::note

    subgraph GM["Gemini / Image Models"]
      direction TB
      G1["Gemini 3.1 Pro Preview<br/>Source Video Analysis"]:::ai
      G2["Gemini 3.1 Pro Preview<br/>Editorial Frame Selection"]:::ai
      G3["Gemini 3.1 Pro Preview<br/>Uploaded Asset Description"]:::ai
      G4["Gemini 3.1 Pro Preview<br/>Editorial Conversation"]:::ai
      G5["Gemini 3.1 Pro Preview<br/>Final Issue Publisher"]:::ai
      GI["Gemini Flash Image family<br/>Backdrop Generation"]:::ai
      GB["Gemini 3.1 Pro Preview<br/>BGM Prompt Blueprint"]:::ai
    end

    subgraph LYR["Separate Vertex Predict path"]
      direction TB
      AUTH["google.auth<br/>OAuth token"]:::note
      LY["Lyria 2 (lyria-002)<br/>Optional Soundtrack Generation"]:::ai
    end
  end

  U --> UI
  Y --> UI
  R --> UI

  UI --> ORCH
  ORCH --> META
  ORCH --> DL
  ORCH --> ASSET
  ORCH --> G1

  Y -.-> G1
  META --> G1

  META --> G4
  META --> G5

  G1 --> CV
  DL --> CV

  CV --> G2
  G1 --> G2

  CV --> SF
  G2 --> SF

  ORCH --> G4
  G1 --> G4
  ASSET --> G4
  G4 --> UI

  ASSET --> G3
  ASSET --> G5
  G3 --> G5
  ORCH --> G5
  G1 --> G5
  SF --> G5
  G4 --> G5

  G5 --> GI
  G5 --> GB
  AUTH --> LY
  GB --> LY

  G5 --> MD
  G1 --> MD
  SF --> MD

  G5 --> HTML
  GI --> HTML
  LY --> HTML
  SF --> HTML
  ASSET --> HTML

  HTML --> PDF

  MD --> UI
  HTML --> UI
  PDF --> UI
  UI --> U

  SDK -.-> G1
  SDK -.-> G2
  SDK -.-> G3
  SDK -.-> G4
  SDK -.-> G5
  SDK -.-> GI
  SDK -.-> GB

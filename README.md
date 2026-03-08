# **🖤 AI Visual Zine Editor**

**AI Visual Zine Editor** is a Streamlit app that analyzes public YouTube videos, curates editorial frames with Google Gemini, lets users refine the interpretation through chat, and exports the result as a magazine-style zine in HTML, PDF, or Markdown.

Users can paste a public YouTube URL, inspect AI-selected editorial frames, discuss the work with a conversational critic, attach their own reference images, and publish the result as a stylized webzine with optional soundtrack generation and export to **HTML**, **PDF**, and **Markdown**.

## **Why this project exists**

A lot of multimodal tools can summarize a video, but very few can turn that material into something that feels **edited**, **authored**, and **publishable**.

This project treats Gemini not just as an analyzer, but as a **collaborative editorial agent**:

* It watches a public YouTube video.  
* It identifies representative visual moments.  
* It supports a back-and-forth editorial conversation.  
* It incorporates user-uploaded reference images into that conversation.  
* It publishes the final result as a designed magazine-style artifact.

The goal is to move from **analysis** to **creative editorial production**.

## **Core features**

### **1\. Public YouTube video analysis**

The app accepts a public YouTube URL and uses **Gemini 3.1 Pro Preview** to:

* classify the source as a music video, fashion show, or other visually driven video,  
* generate a clean display title,  
* produce a structured editorial review,  
* suggest candidate timestamps for representative frames.

### **2\. Automatic frame extraction and curation**

A local media-processing pipeline uses:

* **yt-dlp** for metadata and video retrieval,  
* **OpenCV** for frame extraction and quality scoring.

The app then selects a small editorial frame set for the issue layout.

### **3\. Conversational editorial collaboration**

Inside the **Conversation** tab, the user can discuss the current work with Gemini as if it were a co-editor.

The user can ask for:

* deeper symbolism,  
* comparisons to prior eras or projects,  
* visual grammar analysis,  
* ideological or theoretical readings,  
* layout ideas and editorial emphasis.

These conversation turns are carried into the final publishing stage.

### **4\. User-uploaded reference images**

The user can attach their own images in the chat interface.

These uploaded images are:

* available as context during the editorial conversation,  
* treated as valid magazine assets during publishing,  
* included in the final webzine / HTML / PDF layout with labels and captions.

### **5\. Final issue publishing**

When the user publishes the issue, the app generates:

* a magazine-ready issue title,  
* a deck,  
* a cover line,  
* a pull quote,  
* a substantially expanded editorial text,  
* frame captions,  
* uploaded-image captions,  
* a full multi-page export layout.

### **6\. Generated editorial backdrop**

The issue can generate a decorative visual backdrop using the **Gemini Flash Image** family.

### **7\. Optional background music**

The app can optionally generate an issue soundtrack using **Lyria 2**.

To improve reliability, the music prompt is first distilled into a safer generic mood blueprint before the Lyria call is made.

### **8\. Export formats**

The final issue can be exported as:

* **Markdown**  
* **HTML**  
* **PDF**

The PDF export is designed as a magazine-style multi-page layout rather than a plain text printout.

## **End-to-end workflow**

1. Paste a public YouTube URL.  
2. Let the app analyze the source video and select candidate editorial frames.  
3. Inspect the overview and frame set.  
4. Use the conversation tab to refine the editorial angle.  
5. Upload additional reference images if needed.  
6. Publish the final webzine.  
7. Optionally generate background music.  
8. Download the issue as HTML, PDF, or Markdown.

## **Tech stack**

### **Frontend / app layer**

* **Streamlit**

### **Media processing**

* **yt-dlp**  
* **OpenCV**  
* **Pillow**

### **AI / Google Cloud**

* **Vertex AI**  
* **Gemini 3.1 Pro Preview** for video analysis, conversational editing, and publishing  
* **Gemini Flash Image** family for decorative backdrop generation  
* **Lyria 2** for optional soundtrack generation

### **Export**

* **HTML/CSS** export  
* **WeasyPrint** for PDF rendering

## **Models used**

### **Text / reasoning**

* gemini-3.1-pro-preview

Used for:

* video analysis,  
* frame-selection reasoning,  
* conversational editorial responses,  
* final issue generation,  
* music prompt blueprinting.

### **Image generation**

The app tries a small fallback chain for decorative backdrop generation:

* gemini-3.1-flash-image  
* gemini-3.1-flash-image-preview  
* gemini-2.5-flash-image

### **Music generation**

* lyria-002

Used for optional instrumental background music generation.

## **Data sources and assets**

This project uses the following data sources and user-provided assets:

1. **Public YouTube URLs** supplied by the user  
2. **YouTube metadata and video retrieval** via yt-dlp  
3. **Frames extracted from the source video** via OpenCV  
4. **User-uploaded conversation images** supplied directly inside the chat interface  
5. **AI-generated decorative backdrops** generated during publishing  
6. **AI-generated soundtrack** generated during publishing when enabled

No external private dataset is required for the core workflow.

## **Repository structure**

ai-visual-zine-editor/  
├── app.py  
├── requirements.txt  
├── README.md  
├── architecture\_diagram\_cleaner.md  
└── (optional) exported screenshots / demo assets

## **🚀 Local Setup Instructions**

To run this project locally and test the multimodal pipeline:

### **1\. Clone the repository**

git clone \[https://github.com/kim-chair/ai-visual-zine-editor\](https://github.com/kim-chair/ai-visual-zine-editor)  
cd ai-visual-zine-editor

### **2\. Install dependencies**

Make sure you have **Python 3.9+** installed.

pip install \-r requirements.txt

**Note:** For PDF export to work, your system must also have the platform dependencies required by **WeasyPrint** installed.

WeasyPrint installation guide: https://doc.courtbouillon.org/weasyprint/stable/first\_steps.html

The HTML / webzine view works even if PDF export is unavailable.

### **3\. Authenticate with Google Cloud**

This app relies on Google Cloud Vertex AI. You must authenticate your local environment with a Google Cloud account that has Vertex AI access enabled.

gcloud auth application-default login

### **4\. Confirm or edit your Google Cloud project settings**

The current app.py includes a project ID constant. If you are running this on your own Google Cloud project, update the relevant constants in app.py:

* PROJECT\_ID  
* LOCATION  
* LYRIA\_LOCATION

### **5\. Run the Streamlit app**

streamlit run app.py

If the streamlit command is not available directly, run:

python \-m streamlit run app.py

## **Google Cloud services used**

This project uses Google Cloud services in the following ways:

* **Vertex AI** for Gemini and Lyria model inference  
* **Application Default Credentials** for local authenticated access  
* **Cloud Run** as the intended backend deployment target for hosted execution

## **Architecture**

Please refer to the architecture diagram in this repository:

* architecture\_diagram\_cleaner.md

High-level flow:

1. The user submits a public YouTube URL.  
2. The Streamlit app orchestrates media processing and AI calls.  
3. yt-dlp and OpenCV retrieve and score candidate frames.  
4. Gemini performs video analysis and editorial reasoning.  
5. The user can continue refining the angle in conversation.  
6. Uploaded images become valid layout assets.  
7. The final publisher generates text, captions, and issue structure.  
8. Gemini Flash Image generates an editorial backdrop.  
9. Lyria optionally generates an issue soundtrack.  
10. The exporter produces HTML, PDF, and Markdown outputs.

## **What makes this project multimodal and agentic**

This project is not a single prompt wrapped in a UI.

It combines:

* **video understanding**,  
* **image-aware conversation**,  
* **frame selection**,  
* **editorial reasoning**,  
* **image generation**,  
* **music generation**,  
* **document export**.

It is agentic because the system does more than answer one request:

* it interprets video input,  
* proposes editorial structure,  
* accepts follow-up human guidance,  
* incorporates new visual evidence,  
* and publishes a final artifact.

## **Learnings**

Some practical lessons from building this project:

### **1\. The best multimodal UX is staged**

Trying to do everything in one step makes the system feel slow and fragile. Separating analysis, conversation, and publication produced a more understandable workflow.

### **2\. User-uploaded images dramatically improve grounding**

When users can attach their own images, the editorial conversation becomes more concrete and the final layout feels more intentional.

### **3\. Export matters as much as inference**

A strong final artifact changes how the system is perceived. HTML/PDF output makes the project feel publishable rather than merely analytical.

### **4\. Music prompting benefits from abstraction**

Passing raw editorial text into music generation is less reliable than first distilling the text into generic stylistic descriptors.

## **Limitations**

* Processing time can increase depending on video length, video resolution, and number of uploaded images.  
* Background music generation depends on Lyria availability and may fail under quota, policy, or request constraints.  
* PDF export depends on WeasyPrint and its system-level dependencies.  
* The frame-selection pipeline is heuristic and may not always choose the most editorially ideal frame.  
* The app works best with public YouTube URLs and visually rich source material.

## **Suggested test flow**

A good test run is:

1. Paste a public YouTube music video or fashion show URL.  
2. Review the AI-generated editorial analysis.  
3. Upload several reference images in the conversation tab.  
4. Ask for a sharper editorial angle.  
5. Publish the final webzine.  
6. Download the HTML or PDF issue.

## **Acknowledgements**

Built with:

* Streamlit  
* Google Vertex AI  
* Gemini 3.1 Pro Preview  
* Gemini Flash Image  
* Lyria 2  
* yt-dlp  
* OpenCV  
* WeasyPrint

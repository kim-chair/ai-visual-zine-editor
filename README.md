# 🖤 AI Visual Zine Editor

An interactive, agentic publishing tool that transforms public YouTube videos into highly curated, multi-page digital magazines through real-time collaboration with Gemini 3.1 Pro.

## 🚀 Spin-up Instructions for Judges

To run this project locally and test the multimodal pipeline:

**1. Clone the repository**
`git clone https://github.com/kim-chair/ai-visual-zine-editor`
`cd ai-visual-zine-editor`

**2. Install dependencies**
Make sure you have Python 3.9+ installed.
`pip install -r requirements.txt`

*(Note: For the PDF export feature to work, your system must have [WeasyPrint dependencies](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html) installed. The HTML/Webzine view works universally without it.)*

**3. Authenticate with Google Cloud**
This app relies on Google Cloud Vertex AI (Gemini 3.1 Pro, Gemini Flash Image, and Lyria). You must authenticate your local environment with a GCP account that has Vertex AI enabled.
`gcloud auth application-default login`

**4. Run the Streamlit app**
`streamlit run app.py`

## 🏗️ Architecture 
Please refer to the Architecture Diagram in the repository (or the Devpost submission) to see the full data flow between the user, the OpenCV media pipeline, and the Vertex AI endpoints.

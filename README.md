# court_details_app
A FastAPI web app that retrieves court case details from NDAP datasets and generates downloadable judgment PDFs with filing date, hearing status, and party information.

## 🚀 Features

- 🔍 Search court cases using case number, type, year, and court level  
- 🧠 Loads data from NDAP CSV datasets (e.g., `NDAP_REPORT_*.csv`)  
- 🧰 Manual case overrides for demo entries (like case `8152`)  
- 📦 Stores and logs queries using SQLModel + SQLite  
- 🌐 Frontend UI for quick inputs and results display  
- 🧑‍💻 CORS-enabled for integration with web frontends  



## 🧩 Tech Stack

| Component   | Technology |
|------------ |-------------|
| Backend     | FastAPI (Python) |
| Frontend    | HTML + JavaScript (Fetch API) |
| Database    | SQLite (via SQLModel) |
| PDF Engine  | ReportLab |
| Data Parser | Pandas |
| Server      | Uvicorn |

---

## 🛠️ Setup Instructions

1️⃣ Clone the repository
git clone https://github.com/Srvanthi-293/court_details_app.git
cd court_details_app
2️⃣ Create a virtual environment
python -m venv venv
venv\Scripts\activate    # On Windows
3️⃣ Install dependencies
pip install -r requirements.txt
4️⃣ Create required folders
Make sure these directories exist:
mkdir downloads dataset
5️⃣ Add NDAP datasets
Place your dataset files (like NDAP_REPORT_8152.csv) inside the dataset/ folder.
6️⃣ Run the FastAPI app

uvicorn app:app --reload
Then open in your browser:
👉 http://127.0.0.1:8000
🧾 Example Usage
Example Request
json
Copy code
{
  "case_type": "Civil",
  "case_number": 8152,
  "year": 2020,
  "court_level": "High Court"
}
Example Output
yaml
Copy code
Parties: NDAP 8152 • Row 3
Filing Date: 2020
Next Hearing: 2025-11-15
Status: Disposed
Source: https://ndap.niti.gov.in
The app generates a PDF file (judgment_8152.pdf) stored under /downloads.

🧱 API Endpoints
Method	Endpoint	Description
GET	/ping	Health check endpoint
POST	/cases/lookup	Look up case details by input
POST	/admin/dataset/reload	Reload all NDAP datasets
GET	/dl/file/{filename}	Download generated PDF
GET	/datasets/list	List available NDAP datasets

📂 Folder Structure
bash
Copy code
court_details_app/
│
├── app.py               # Main FastAPI application
├── models.py            # Database models
├── storage/             # Database session utilities
├── dataset/             # NDAP CSV data
├── downloads/           # Generated PDFs
├── index.html           # Frontend user interface
├── requirements.txt     # Python dependencies
└── README.md            # Documentation

⚠️ Limitations
⚠️ Live eCourts scraping is not available (requires captcha bypass)

📁 Works only with local NDAP dataset CSV files

📄 Generated PDFs are simple, readable layouts

🧠 Demo case 8152 provided for consistent testing

💡 Future Enhancements
Integration with live eCourts APIs

Add official court header/logo on PDFs

Dashboard to view search history

Docker containerization for easy deployment

👩‍💻 Author
Sruvanthi
📧 GitHub Profile

🪪 License
This project is licensed under the MIT License.
You are free to use, modify, and distribute it for learning or demonstration purposes.




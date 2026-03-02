# 🎴 Card Placement Analysis - Flask Web Application

A comprehensive web application for analyzing card sorting behavior with interactive visualizations, pattern discovery, and animated trial playback.

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![Flask](https://img.shields.io/badge/flask-3.0.0-green.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

## 📋 Features

### 🏠 Dashboard
- **Overview Statistics**: Total trials, participants, success rate
- **Visual Metrics**: Animated stat cards, progress bars, color-coded indicators
- **Condition Breakdown**: Display all experimental conditions

### 🔍 Trial Explorer
- **Interactive Selection**: Dropdown menus for participant and trial selection
- **Animated Playback**: Step-by-step card placement animations with HTML5 controls
- **Static Views**: Final state visualization with detailed trial information
- **Real-time Info**: Condition, total moves, and success/failure status

### 📊 Pattern Analysis
- **Top 5 Patterns**: Most frequent final positions for success and failure cases
- **Visual Grids**: Color-coded card representations with suit symbols
- **Interactive Selector**: Choose patterns and view matching trials
- **Animated Examples**: See how participants achieved each pattern

### 🎨 Visualizations
- **Color-Coded Cards**: Queen (Red), King (Teal), Jack (Blue), Blank (Green)
- **Suit Symbols**: ♠ ♥ ♦ ♣
- **8x8 Grid**: Labeled rows (1-8) and columns (A-H)
- **Thread-Safe Rendering**: Concurrent image generation without race conditions

## 🚀 Quick Start

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

### Installation

1. **Clone or download the repository**

```bash
cd flask_card_analysis
```

2. **Create virtual environment (recommended)**

```bash
python -m venv venv

# Activate on Windows
venv\Scripts\activate

# Activate on Mac/Linux
source venv/bin/activate
```

3. **Install dependencies**

```bash
pip install -r requirements.txt
```

4. **Add your dataset**

```bash
cp /path/to/your/CardsDataset.csv data/
```

5. **Run the application**

```bash
python app.py
```

6. **Open your browser**

Visit: **http://localhost:5000**

## 📁 Project Structure

```
flask_card_analysis/
├── app.py                          # Main Flask application
├── requirements.txt                # Python dependencies
├── README.md                       # This file
├── .gitignore                      # Git ignore rules
│
├── templates/                      # HTML templates
│   ├── base.html
│   ├── index.html
│   ├── explorer.html
│   ├── patterns.html
│   └── error.html
│
├── static/
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   ├── main.js
│   │   ├── explorer.js
│   │   └── patterns.js
│   └── animations/                 # Auto-generated
│
└── data/
    └── CardsDataset.csv            # Your dataset
```

## 📊 Data Requirements

Your `CardsDataset.csv` must include:
- `participant` - Participant ID
- `trialN` - Trial number
- `condition` - Experimental condition
- `overall_correct` - Success flag (1/0)
- `movement_codes` - Card movements
- `final_card_position_codes_1` - Final positions

## 🛠️ Configuration

### Change Port

```python
# app.py (last line)
app.run(debug=True, port=5001)
```

### Modify Colors

```python
# app.py (lines ~45-50)
self.card_colors = {
    'queen': '#FF6B6B',    # Your color
    'king': '#4ECDC4',
    'jack': '#45B7D1',
    'blank': '#95E1D3'
}
```

### Animation Speed

```python
# app.py (line ~355)
interval=500,  # Milliseconds
```

## 🐛 Troubleshooting

### Dataset Not Loading
- Verify file exists in `data/` folder
- Check CSV format matches requirements

### Port Already in Use
```bash
# Kill process on port 5000
lsof -ti:5000 | xargs kill -9
```

### Images Not Displaying
- Restart Flask application
- Clear browser cache
- Check browser console for errors

## 🚀 Deployment

### Development
```bash
python app.py
```

### Production (Gunicorn)
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### Docker
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
```

## 📚 API Endpoints

- `GET /` - Homepage
- `GET /explorer` - Trial explorer
- `GET /patterns` - Pattern analysis
- `GET /api/get-trials/<participant>` - Get trials
- `GET /api/trial-info/<participant>/<trial>` - Trial details
- `GET /api/generate-animation/<participant>/<trial>` - Animation
- `GET /api/trial-image/<participant>/<trial>` - Static image
- `GET /api/analyze-patterns/<type>` - Pattern analysis
- `GET /api/pattern-image/<type>/<id>` - Pattern visualization
- `GET /api/pattern-trials/<type>/<id>` - Matching trials

## 🔒 Security

### Production Checklist
- [ ] Change `SECRET_KEY`
- [ ] Set `debug=False`
- [ ] Use HTTPS
- [ ] Implement rate limiting
- [ ] Add authentication

## 📝 License

MIT License - See LICENSE file for details

## 🙏 Acknowledgments

Built with Flask, Matplotlib, and Pandas

---

**Version:** 1.0.2  
**Last Updated:** February 2026  
**Status:** Production Ready ✅

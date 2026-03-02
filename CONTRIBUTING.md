# Contributing to Card Placement Analysis

Thank you for considering contributing to this project! 🎉

## How to Contribute

### Reporting Bugs

If you find a bug, please create an issue with:

- **Clear title** describing the issue
- **Steps to reproduce** the problem
- **Expected behavior** vs actual behavior
- **Screenshots** if applicable
- **Environment details** (OS, Python version, browser)

### Suggesting Features

Feature requests are welcome! Please provide:

- **Use case** - why is this needed?
- **Proposed solution** - how should it work?
- **Alternatives** - other options you've considered

### Code Contributions

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feature-name`
3. **Make your changes**
4. **Test thoroughly**
5. **Commit with clear messages**: `git commit -m "Add feature X"`
6. **Push to your fork**: `git push origin feature-name`
7. **Create a Pull Request**

## Development Setup

```bash
# Clone your fork
git clone https://github.com/your-username/flask_card_analysis.git
cd flask_card_analysis

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Run in development mode
python app.py
```

## Code Style

### Python
- Follow PEP 8
- Use meaningful variable names
- Add docstrings to functions
- Keep functions focused and small

### JavaScript
- Use ES6+ features
- Comment complex logic
- Use async/await for promises
- Handle errors gracefully

### HTML/CSS
- Semantic HTML5 elements
- Accessible markup (ARIA labels)
- Mobile-first responsive design
- Consistent indentation (2 spaces)

## Testing

Before submitting:

- [ ] Code runs without errors
- [ ] All features work as expected
- [ ] No console errors in browser
- [ ] Tested on multiple browsers
- [ ] Mobile responsive
- [ ] No broken links or images

## Pull Request Guidelines

**Title:** Clear, descriptive summary

**Description should include:**
- What changes were made
- Why these changes are needed
- Any breaking changes
- Screenshots (if UI changes)

**Example:**
```markdown
## Add export to PDF feature

### Changes
- Added new route `/export/pdf/<participant>/<trial>`
- Created PDF generation using ReportLab
- Added "Export PDF" button to trial explorer

### Why
Users requested ability to save trial visualizations as PDF for reports.

### Breaking Changes
None

### Screenshots
[Include screenshot of new button]
```

## Commit Message Format

```
type: Brief description

Longer explanation if needed.

Fixes #123
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Code formatting
- `refactor`: Code restructuring
- `test`: Adding tests
- `chore`: Maintenance

## Areas Needing Help

- [ ] Unit tests
- [ ] Documentation improvements
- [ ] Performance optimization
- [ ] Accessibility enhancements
- [ ] Mobile UX improvements
- [ ] Browser compatibility testing

## Questions?

Feel free to open an issue labeled "question" or reach out to the maintainers.

Thank you for contributing! 🙏

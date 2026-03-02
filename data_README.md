# Data Folder

## Purpose

This folder contains the card sorting dataset used by the Flask application.

## Required File

Place your `CardsDataset.csv` file in this directory.

## File Format

The CSV should contain these columns:

| Column | Type | Description | Example |
|--------|------|-------------|---------|
| participant | int | Participant ID | 57 |
| trialN | int | Trial number | 0 |
| condition | string | Experimental condition | "control" |
| overall_correct | int | Success flag | 1 or 0 |
| movement_codes | list (as string) | Card movements | "['queen_spades_A1', ...]" |
| final_card_position_codes_1 | list (as string) | Final positions | "['queen_spades_A1', ...]" |

## Movement Format

Each movement string follows the pattern: `"card_suit_position"`

**Examples:**
- `"queen_spades_A1"` - Queen of Spades at position A1
- `"king_hearts_D4"` - King of Hearts at position D4
- `"jack_clubs_Off Grid"` - Jack of Clubs removed from grid

## Adding Your Dataset

```bash
# Copy your file to this folder
cp /path/to/your/CardsDataset.csv ./

# Or on Windows
copy C:\path\to\your\CardsDataset.csv .
```

## Security Note

⚠️ **Important:** This folder is excluded from Git by default (see `.gitignore`)

The dataset likely contains sensitive research data and should not be committed to version control.

## File Size

- Expected size: Varies based on number of trials
- Typical range: 500KB - 50MB
- Large files (>100MB) may require optimization

## Troubleshooting

### File Not Found
- Ensure the file is named exactly `CardsDataset.csv`
- Check file permissions (should be readable)
- Verify you're in the correct directory

### Encoding Issues
- CSV should be UTF-8 encoded
- Use Excel "Save As" → "CSV UTF-8" if needed

### Format Errors
- Ensure list columns are properly formatted as strings
- Check for missing required columns
- Validate data types match requirements

## Sample Data Structure

```csv
participant,trialN,condition,overall_correct,movement_codes,final_card_position_codes_1
57,0,control,1,"['queen_spades_A1', 'king_hearts_B2']","['queen_spades_A1', 'king_hearts_B2']"
57,1,control,0,"['jack_clubs_C3']","['jack_clubs_Off Grid']"
```

---

**Need help?** Check the main README.md or contact support.

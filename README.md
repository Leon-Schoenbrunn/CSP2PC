
# CSP → Procreate Brush Converter

Convert Clip Studio Paint `.sut` brushes to Procreate `.brush` or `.brushset` files with automatic mapping for many brush settings.  
Supports both **CLI usage** and **standalone executable** mode with file picker.

⚠️ *Note: Some CSP-specific functionality has no direct equivalent in Procreate and cannot be mapped. This script is by no means a perfect 1:1 conversion.*

##  Features

- **Automatic brush setting mapping** for:
  - Stroke Path (spacing, jitter)
  - Taper (pressure & touch)
  - Shape rendering
  - Wet mix
  - Basic brush properties (opacity, rotation, scatter, flip, etc.)
- **Preserves multiple brush tips** in a `.brushset`
- **CLI or double-click executable** usage
- Executable works on **Windows** and **macOS** (with Python or packaged exe)

##  Usage

### 1. Command-line
```bash
python csp2procreate.py mybrush.sut output_directory
```

### 2. Packaged Executable
- Double-click the [executable](https://github.com/Leon-Schoenbrunn/CSP2PC/releases/latest)
- Select your `.sut` file and output directory

## Output

- **Single brush** → `.brush` file  
- **Multiple brush tips** → `.brushset` bundle  

## Limitations

Some CSP brush features cannot be fully represented in Procreate, including:
- Special texture effects
- Dynamic color blending beyond Wet Mix
- Custom pattern behavior
- Multi-brushes and Pattern Sequencing

## Installation (Python)

Clone the repository:
```bash
git clone https://github.com/yourname/csp2procreate.git
cd CSP2PC
```

Install dependencies:
```bash
pip install -r requirements.txt
```

Run:
```bash
python csp2procreate.py mybrush.sut output_dir
```

## License

Licensed under the [MIT License](LICENSE).


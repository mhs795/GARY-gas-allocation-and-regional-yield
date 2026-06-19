# Gas Market Model Installation Guide

Follow these steps to set up the Gas Market Optimization Model on your local machine.

## Prerequisites
- Python 3.10+
- `pip` package manager

## Installation Steps
1. **Navigate to the project directory:**
   ```bash
   cd gas_market_model
   ```

2. **Set up a virtual environment (optional but recommended):**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Linux/macOS
   # or
   venv\Scripts\activate     # On Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch the model:**
   - **Linux/macOS:**
     ```bash
     ./run_dashboard.sh
     ```
   - **Windows:**
     ```cmd
     run_dashboard.bat
     ```

## Troubleshooting
- If dependencies fail to install, ensure your pip version is updated (`pip install --upgrade pip`).
- Ensure the HiGHS solver is accessible if you plan to run large optimizations.

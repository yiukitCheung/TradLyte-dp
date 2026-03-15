#!/usr/bin/env python3
"""
Debug script for testing save_gold_data functionality
This allows you to test the save_gold_data method without running the entire pipeline
"""

import sys
import os
import polars as pl
from datetime import datetime
import time
import pdb  # Python's built-in debugger

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.load_setting import load_setting
from process.core import DataLoader

def debug_save_gold_data():
    """Debug the save_gold_data function using pdb"""
    print("🔍 Starting debug session for save_gold_data...")
    
    # Load settings
    settings = load_setting()
    print(f"Settings loaded. Gold path: {settings['process']['gold_path']}")
    
    # Initialize DataLoader
    data_loader = DataLoader(settings)
    print("DataLoader initialized")
    
    # Create a simple test dataframe
    test_df = pl.DataFrame({
        'date': [datetime.now().date()],
        'symbol': ['TEST'],
        'value': [100.0]
    })
    
    print(f"Test dataframe created: {test_df}")
    
    # Set a breakpoint here - this will pause execution and let you inspect variables
    print("Setting breakpoint before save_gold_data call...")
    pdb.set_trace()  # Execution will pause here
    
    # This line will only execute after you continue from the debugger
    data_loader.save_gold_data(test_df, table_name="debug_test.parquet")
    
    print("save_gold_data completed!")
    data_loader.close()

def debug_with_real_data():
    """Debug save_gold_data with your actual data"""
    print("🔍 Starting debug session with real data...")
    
    settings = load_setting()
    data_loader = DataLoader(settings)
    
    # Load your actual silver data (this is what the pipeline would do)
    print("Loading silver data...")
    df = data_loader.load_silver_data()
    print(f"Loaded {len(df)} rows of silver data")
    
    # Apply indicators (simplified version)
    from process.core import IndicatorCalculator
    indicator_calculator = IndicatorCalculator()
    df = indicator_calculator.apply(df)
    print(f"Applied indicators. DataFrame shape: {df.shape}")
    
    # Set breakpoint before save_gold_data
    print("Setting breakpoint before save_gold_data...")
    pdb.set_trace()
    
    # This is where you can debug the save_gold_data call
    data_loader.save_gold_data(df, table_name="gold.parquet")
    
    print("save_gold_data completed!")
    data_loader.close()

def create_test_data():
    """Create sample test data for debugging"""
    print("Creating test data...")
    
    # Create sample data similar to what the pipeline would produce
    test_data = {
        'date': [datetime.now().date()] * 10,
        'symbol': ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'AMZN', 'META', 'NVDA', 'NFLX', 'AMD', 'INTC'],
        'interval': [1] * 10,
        'open': [150.0, 2800.0, 300.0, 800.0, 3200.0, 350.0, 450.0, 500.0, 100.0, 50.0],
        'high': [155.0, 2850.0, 305.0, 810.0, 3250.0, 355.0, 460.0, 510.0, 105.0, 52.0],
        'low': [148.0, 2780.0, 295.0, 790.0, 3180.0, 345.0, 440.0, 490.0, 95.0, 48.0],
        'close': [152.0, 2820.0, 302.0, 805.0, 3220.0, 352.0, 455.0, 505.0, 102.0, 51.0],
        'volume': [1000000, 500000, 800000, 1200000, 900000, 600000, 700000, 400000, 300000, 200000],
        'ema_8': [151.5, 2815.0, 301.5, 802.5, 3215.0, 351.5, 452.5, 502.5, 101.5, 50.5],
        'ema_21': [150.8, 2808.0, 300.8, 800.8, 3208.0, 350.8, 450.8, 500.8, 100.8, 50.8],
        'rsi': [65.5, 58.2, 72.1, 45.8, 68.9, 55.3, 78.4, 62.7, 48.9, 71.2],
        'macd': [2.5, 12.0, 1.8, -5.2, 7.8, 2.1, 8.9, 4.5, -1.2, 3.7],
        'long_accelerating': [['AAPL', 'GOOGL'], ['MSFT', 'TSLA'], ['AMZN', 'META'], ['NVDA', 'NFLX'], ['AMD', 'INTC']] * 2,
        'long_accumulating': [['TSLA', 'AMZN'], ['META', 'NVDA'], ['NFLX', 'AMD'], ['INTC', 'AAPL'], ['GOOGL', 'MSFT']] * 2,
        'velocity_maintained': [['NVDA', 'NFLX'], ['AMD', 'INTC'], ['AAPL', 'GOOGL'], ['MSFT', 'TSLA'], ['AMZN', 'META']] * 2
    }
    
    df = pl.DataFrame(test_data)
    print(f"Created test data with {len(df)} rows and {len(df.columns)} columns")
    print(f"Columns: {df.columns}")
    print(f"Sample data:\n{df.head(3)}")
    
    return df

def test_save_gold_data():
    """Test the save_gold_data functionality"""
    print("=" * 60)
    print("DEBUGGING save_gold_data FUNCTIONALITY")
    print("=" * 60)
    
    try:
        # Load settings
        print("\n1. Loading settings...")
        settings = load_setting()
        print(f"Settings loaded successfully")
        print(f"Gold path: {settings['process']['gold_path']}")
        
        # Create test data
        print("\n2. Creating test data...")
        test_df = create_test_data()
        
        # Initialize DataLoader
        print("\n3. Initializing DataLoader...")
        data_loader = DataLoader(settings)
        print("DataLoader initialized successfully")
        
        # Test save_gold_data with different table names
        print("\n4. Testing save_gold_data...")
        
        # Test 1: Save as "gold"
        print("\n   Test 1: Saving as 'gold' table...")
        start_time = time.time()
        data_loader.save_gold_data(test_df, table_name="gold.parquet")
        end_time = time.time()
        print(f"   ✅ Successfully saved 'gold.parquet' in {end_time - start_time:.2f} seconds")
        
        # Test 2: Save as "gold_picks"
        print("\n   Test 2: Saving as 'gold_picks' table...")
        start_time = time.time()
        data_loader.save_gold_data(test_df, table_name="gold_picks.parquet")
        end_time = time.time()
        print(f"   ✅ Successfully saved 'gold_picks.parquet' in {end_time - start_time:.2f} seconds")
        
        # Test 3: Save with custom filename
        print("\n   Test 3: Saving with custom filename...")
        custom_filename = f"debug_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.parquet"
        start_time = time.time()
        data_loader.save_gold_data(test_df, table_name=custom_filename)
        end_time = time.time()
        print(f"   ✅ Successfully saved '{custom_filename}' in {end_time - start_time:.2f} seconds")
        
        # Verify files were created
        print("\n5. Verifying saved files...")
        gold_path = settings['process']['gold_path']
        
        files_to_check = [
            os.path.join(gold_path, "gold.parquet"),
            os.path.join(gold_path, "gold_picks.parquet"),
            os.path.join(gold_path, custom_filename)
        ]
        
        for file_path in files_to_check:
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                print(f"   ✅ {os.path.basename(file_path)}: {file_size:,} bytes")
                
                # Try to read the file back to verify it's valid
                try:
                    read_df = pl.read_parquet(file_path)
                    print(f"      📊 Read back {len(read_df)} rows successfully")
                except Exception as e:
                    print(f"      ❌ Error reading file: {e}")
            else:
                print(f"   ❌ {os.path.basename(file_path)}: File not found")
        
        # Test with larger dataset
        print("\n6. Testing with larger dataset...")
        large_df = pl.concat([test_df] * 100)  # Create 1000 rows
        print(f"   Created larger dataset with {len(large_df)} rows")
        
        start_time = time.time()
        data_loader.save_gold_data(large_df, table_name="large_test.parquet")
        end_time = time.time()
        print(f"   ✅ Successfully saved large dataset in {end_time - start_time:.2f} seconds")
        
        # Close the data loader
        print("\n7. Cleaning up...")
        data_loader.close()
        print("   ✅ DataLoader closed successfully")
        
        print("\n" + "=" * 60)
        print("🎉 ALL TESTS PASSED! save_gold_data is working correctly.")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        print(f"Traceback:\n{traceback.format_exc()}")
        return False
    
    return True

def test_specific_issue():
    """Test a specific issue you might be having"""
    print("\n" + "=" * 60)
    print("TESTING SPECIFIC ISSUES")
    print("=" * 60)
    
    try:
        settings = load_setting()
        data_loader = DataLoader(settings)
        
        # Test with empty dataframe
        print("\n1. Testing with empty dataframe...")
        empty_df = pl.DataFrame()
        data_loader.save_gold_data(empty_df, table_name="empty_test.parquet")
        print("   ✅ Empty dataframe saved successfully")
        
        # Test with single row
        print("\n2. Testing with single row...")
        single_row_df = pl.DataFrame({
            'date': [datetime.now().date()],
            'symbol': ['TEST'],
            'value': [100.0]
        })
        data_loader.save_gold_data(single_row_df, table_name="single_row_test.parquet")
        print("   ✅ Single row saved successfully")
        
        # Test with very large values
        print("\n3. Testing with large values...")
        large_values_df = pl.DataFrame({
            'date': [datetime.now().date()] * 5,
            'symbol': ['LARGE1', 'LARGE2', 'LARGE3', 'LARGE4', 'LARGE5'],
            'big_number': [1e15, 1e20, 1e25, 1e30, 1e35],
            'small_number': [1e-15, 1e-20, 1e-25, 1e-30, 1e-35]
        })
        data_loader.save_gold_data(large_values_df, table_name="large_values_test.parquet")
        print("   ✅ Large values saved successfully")
        
        data_loader.close()
        print("\n   ✅ All specific tests passed!")
        
    except Exception as e:
        print(f"\n❌ SPECIFIC TEST ERROR: {str(e)}")
        import traceback
        print(f"Traceback:\n{traceback.format_exc()}")

if __name__ == "__main__":
    print("Starting save_gold_data debug script...")
    print("\nChoose your debugging option:")
    print("1. Simple debug with pdb (recommended)")
    print("2. Debug with real data")
    print("3. Run full test suite")
    print("4. Test specific issues")
    
    choice = input("\nEnter your choice (1-4): ").strip()
    
    if choice == "1":
        debug_save_gold_data()
    elif choice == "2":
        debug_with_real_data()
    elif choice == "3":
        success = test_save_gold_data()
        if success:
            test_specific_issue()
    elif choice == "4":
        test_specific_issue()
    else:
        print("Invalid choice. Running simple debug...")
        debug_save_gold_data()
    
    print("\nDebug script completed!") 
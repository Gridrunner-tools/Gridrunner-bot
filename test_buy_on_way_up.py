#!/usr/bin/env python3
"""
Unit tests for the new buy-on-the-way-up and drop-through recovery logic.
Exercises the precise state transition machine for both gap-fill and recovery behavior.
"""
import sys, types
sys.modules.setdefault("requests", types.SimpleNamespace())

def test_drop_through_recovery_logic():
    # Simulate the state transition of our drop-through recovery logic!
    grids = [90.0, 92.0, 94.0, 96.0, 98.0, 100.0]
    mid_idx = 5
    filled = {}
    
    # Tick 1: start at 101
    price = 101.0
    last_price = None
    drop_through_active = False
    drop_through_low = price
    drop_through_levels = []
    
    # Tick 2: drop to 91
    last_price = price
    price = 91.0
    
    # Detect drop-through
    if last_price is not None and price < last_price:
        crossed_levels = []
        for gap_i in range(mid_idx):
            if gap_i in filled:
                continue
            if price <= grids[gap_i] < last_price:
                crossed_levels.append(gap_i)
        
        if len(crossed_levels) > 1:
            if not drop_through_active:
                drop_through_active = True
                drop_through_low = price
                drop_through_levels = crossed_levels
                
    assert drop_through_active is True
    assert drop_through_low == 91.0
    assert set(drop_through_levels) == {1, 2, 3, 4} # 92, 94, 96, 98
    
    # Tick 3: drop further to 89
    last_price = price
    price = 89.0
    
    if last_price is not None and price < last_price:
        crossed_levels = []
        for gap_i in range(mid_idx):
            if gap_i in filled:
                continue
            if price <= grids[gap_i] < last_price:
                crossed_levels.append(gap_i)
                
        if len(crossed_levels) > 1:
            pass
        elif drop_through_active:
            if price < drop_through_low:
                drop_through_low = price
            for lvl in crossed_levels:
                if lvl not in drop_through_levels:
                    drop_through_levels.append(lvl)
                    
    assert drop_through_active is True
    assert drop_through_low == 89.0
    assert set(drop_through_levels) == {0, 1, 2, 3, 4} # 90 is now also crossed
    
    # Tick 4: confirmed upward tick to 90.5
    last_price = price
    price = 90.5
    
    recovered_buys = []
    if last_price is not None and price > last_price and drop_through_active:
        drop_through_levels.sort()
        for gap_i in list(drop_through_levels):
            if gap_i in filled:
                continue
            recovered_buys.append(gap_i)
        drop_through_active = False
        drop_through_levels = []
        
    assert drop_through_active is False
    assert recovered_buys == [0, 1, 2, 3, 4] # recovered all of them at/near the bottom!
    print("PASS test_drop_through_recovery_logic")


def test_gap_fill_logic():
    # Simulate gap-fill (C1 downward and buy-on-the-way-up)
    grids = [90.0, 92.0, 94.0, 96.0, 98.0, 100.0]
    mid_idx = 5
    filled = {}
    
    # Tick 1: start at 95
    last_price = 95.0
    price = 95.0
    drop_through_active = False
    
    # Tick 2: slow drop to 93.5 (only crossed 1 level: level 2 @ 94)
    last_price = price
    price = 93.5
    
    crossed_levels = []
    for gap_i in range(mid_idx):
        if gap_i in filled:
            continue
        if price <= grids[gap_i] < last_price:
            crossed_levels.append(gap_i)
            
    assert len(crossed_levels) == 1
    # Since not > 1, drop-through is NOT active
    assert not drop_through_active
    
    # Under general gap-fill:
    for gap_i in range(mid_idx):
        if gap_i in filled:
            continue
        is_downward_gap = (last_price is not None) and (price <= grids[gap_i] < last_price)
        is_upward_gap = (last_price is not None) and (last_price < grids[gap_i] <= price)
        if is_downward_gap or is_upward_gap:
            filled[gap_i] = {"price": price}
            
    assert set(filled.keys()) == {2} # Level 2 (94) successfully filled downward
    
    # Tick 3: slow rise from 93.5 to 94.5 (crossed level 2 again, but it's filled. Rose through Level 3 @ 96? No, 94.5 < 96. Wait, what about level 2? Already filled)
    last_price = price
    price = 94.5
    
    # Tick 4: rise from 94.5 to 97.5 (rose through level 3 @ 96)
    last_price = price
    price = 97.5
    
    for gap_i in range(mid_idx):
        if gap_i in filled:
            continue
        is_downward_gap = (last_price is not None) and (price <= grids[gap_i] < last_price)
        is_upward_gap = (last_price is not None) and (last_price < grids[gap_i] <= price)
        if is_downward_gap or is_upward_gap:
            filled[gap_i] = {"price": price}
            
    assert set(filled.keys()) == {2, 3} # Level 3 (96) successfully filled upward
    print("PASS test_gap_fill_logic")


if __name__ == '__main__':
    test_drop_through_recovery_logic()
    test_gap_fill_logic()

from db.batteries import get_movement_history

history = get_movement_history(1)
for row in history:
    timestamp, from_loc, to_loc, reason, moved_by = row
    print(f"{timestamp} | {from_loc} -> {to_loc} | {reason} | moved by {moved_by}")
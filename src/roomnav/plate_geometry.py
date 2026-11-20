"""Image-space hypotheses shared by M4 evidence collection and M3 advice."""

def plate_in_door_coordinates(sign, door):
    """Measure the plate relative to the door, including adjacent wall plates."""
    sx, sy, ex, ey = sign.bbox_xyxy_norm
    x, y, right, bottom = door.bbox_xyxy_norm
    width, height = right - x, bottom - y
    return ((sx - x) / width, (sy - y) / height,
            (ex - x) / width, (ey - y) / height)


def possible_owners(sign, candidates, *, wall_plates):
    sx, sy, ex, ey = sign.bbox_xyxy_norm
    owners = []
    for door in candidates:
        if door.category != "door":
            continue
        x, y, right, bottom = door.bbox_xyxy_norm
        if x <= sx and ex <= right and y <= sy and ey <= bottom:
            owners.append((door.track_id, "containment"))
        elif wall_plates:
            # Count partial overlaps as competitors as well. A plaque between
            # two jambs cannot establish ownership by nearest-center distance.
            h, w = bottom - y, right - x
            gap = max(x - ex, sx - right, 0)
            if (y + .12*h <= sy < ey <= y + .75*h and gap <= .33*w
                    and ex-sx <= .65*w and ey-sy <= .20*h):
                owners.append((door.track_id, "adjacent_wall"))
    return owners

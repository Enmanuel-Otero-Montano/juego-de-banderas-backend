from io import BytesIO
from PIL import Image


def pixelate_image(image_bytes: bytes, reveal_level: int) -> bytes:
    """
    Apply pixelation/mosaic effect to an image based on reveal level.
    
    Args:
        image_bytes: Original image as bytes (PNG, JPG, etc.)
        reveal_level: Integer 0-6 representing the reveal progression
                      0-1: Very heavy pixelation (16x16 blocks)
                      2: Medium-heavy pixelation (12x12 blocks)
                      3: Medium pixelation (8x8 blocks)
                      4: Light pixelation (4x4 blocks)
                      5: Very light pixelation (2x2 blocks)
                      6+: Original image (no pixelation)
    
    Returns:
        Processed image as PNG bytes
    """
    # Load the image
    img = Image.open(BytesIO(image_bytes))
    
    # Convert to RGB if necessary (handles RGBA, P, etc.)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Determine block size based on reveal level
    if reveal_level <= 1:
        block_size = 16
    elif reveal_level == 2:
        block_size = 12
    elif reveal_level == 3:
        block_size = 8
    elif reveal_level == 4:
        block_size = 4
    elif reveal_level == 5:
        block_size = 2
    else:
        # reveal_level >= 6: return original
        output = BytesIO()
        img.save(output, format='PNG')
        return output.getvalue()
    
    # Apply pixelation by downscaling and upscaling
    original_size = img.size
    
    # Calculate downscaled size
    small_size = (
        max(1, original_size[0] // block_size),
        max(1, original_size[1] // block_size)
    )
    
    # Resize down (averaging pixels into blocks)
    img_small = img.resize(small_size, Image.Resampling.BILINEAR)
    
    # Resize back up (creating the mosaic effect)
    img_pixelated = img_small.resize(original_size, Image.Resampling.NEAREST)
    
    # Convert to bytes
    output = BytesIO()
    img_pixelated.save(output, format='PNG')
    return output.getvalue()

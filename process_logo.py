from PIL import Image

def remove_background(input_path, output_path):
    try:
        img = Image.open(input_path)
        img = img.convert("RGBA")
        datas = img.getdata()

        new_data = []
        # Target background color (Cream/White)
        # Based on previous analysis: #fcfcf9 (252, 252, 249) and #ffffff
        # We will treat anything very light as transparent.

        threshold = 200 # Brightness threshold

        for item in datas:
            # item is (R, G, B, A)
            # Check if pixel is light (background)
            if item[0] > threshold and item[1] > threshold and item[2] > threshold:
                new_data.append((255, 255, 255, 0)) # Transparent
            else:
                new_data.append(item)

        img.putdata(new_data)
        img.save(output_path, "PNG")
        print(f"Successfully saved transparent image to {output_path}")

    except Exception as e:
        print(f"Error processing image: {e}")

if __name__ == "__main__":
    remove_background('static/img/iMDiz.png', 'static/img/iMDiz_transparent.png')

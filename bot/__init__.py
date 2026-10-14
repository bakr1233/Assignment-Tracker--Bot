"""Assignment tracker bot."""

import warnings

# macOS ships LibreSSL, which makes urllib3 grumble on import. Nothing here
# needs the newer OpenSSL, and the warning only confuses the output.
warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL")

__version__ = "1.0.0"

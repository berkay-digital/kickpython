from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="kickpython",
    version="0.1.0",
    description="Python wrapper for Kick.com API",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="berkay.digital",
    author_email="contact@berkay.digital",
    url="https://github.com/berkay-digital/kickpython",
    packages=find_packages(exclude=["tests.*", "tests", "examples.*", "examples"]),
    package_data={"kickpython": ["py.typed"]},
    install_requires=[
        "aiohttp>=3.11.13",
        "websockets>=15.0",
        "asyncio>=3.4.3",
        "curl_cffi",
        "python-dotenv>=1.0.1"
    ],
    python_requires=">=3.6",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords=["kick.com", "api", "wrapper", "streaming", "chat"],
)
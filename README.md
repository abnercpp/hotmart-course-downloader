# Hotmart Course Downloader

Download contents from your Hotmart courses.

> [!CAUTION]
> This project is simply for downloading content you already have access to. **Do not use it for piracy!**

## Running the project

Before running the program, you need to set up your Hotmart SSO credentials in
the [config/credentials.toml](config/credentials.toml) file.

Once your credentials are set, you can run the following in your shell:

> [!TIP]
> You will need [Python 3.12 or newer](https://www.python.org/downloads/) to run this project.

```shell
pip install -r requirements.txt
python src/hotmart_course_downloader/main.py
```

## Nature of this project

I provide no guarantee that this project will be actively maintained in the future, and it might break once Hotmart's
website code changes. It was mostly something I assembled because I needed to download the contents from a course for
offline studies.

This is simply a hobby/toy project.

## License

This personal project is released under the [MIT license](LICENSE).

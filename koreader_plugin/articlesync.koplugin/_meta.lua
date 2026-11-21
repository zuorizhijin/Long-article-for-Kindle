local _ = require("gettext")
return {
    fullname = _("文章同步"),
    description = _([[从电脑端局域网服务同步未读文章到 /mnt/us/documents/文章/，并在读完后自动归档到 /mnt/us/documents/已读/。]]),
}

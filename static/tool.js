function playVideoLocalfile(filename) {
  url = window.location.protocol + "//" + window.location.host + '/ffmpeg/streaming?mode=file&value=' + filename;
  document.getElementById("video_modal_title").innerHTML = filename;
  str = '<video oncontextmenu="return false;" id="video_modal_video" width="766" controls autoplay muted preload="metadata">';
  console.log(url);
  if (filename.endsWith('.mp4')) {
    str += '<source src="' + url + '" type="video/mp4"></video>';
  } else {
    str += '<source src="' + url + '" type="video/webm"></video>';
  }
  document.getElementById("video_player_body").innerHTML = str;
  $("#video_modal").modal();
}


$("body").on('click', '#json_btn', function(e){
  e.preventDefault();
  var idx = $(this).data('idx');
  var tmp = idx.split('_');
  var idx1 = parseInt(tmp[0]);
  var idx2 = parseInt(tmp[1]);
  m_modal(current_data.data[idx1].files[idx2].info);
});

function send_command(name, command, arg1, arg2) {
  $.ajax({
    url: '/' + package_name + '/ajax/' + sub + '/send_command',
    type: "POST", 
    cache: false,
    data:{name:name, command:command, arg1:arg1, arg2:arg2},
    dataType: "json",
    success: function (ret) {
      if (ret.msg != null) notify(ret.msg, ret.ret);
    }
  });
}

$("body").on('click', '#play_btn', function(e){
  e.preventDefault();
  var filename = $(this).data('filename');
  playVideoLocalfile(filename);
});
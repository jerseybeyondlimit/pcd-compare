// script.js

const API_BASE = "https://your-backend-domain.com";  // 后端部署域名

$(document).ready(function () {
  let potreeViewer = null;

  $("#compare-btn").on("click", async function () {
    const baseFileInput = document.getElementById("base-file");
    const genFileInput = document.getElementById("gen-file");

    if (baseFileInput.files.length === 0 || genFileInput.files.length === 0) {
      alert("请先选择 Base PCD 和 Gen PCD 文件");
      return;
    }

    const baseFile = baseFileInput.files[0];
    const genFile = genFileInput.files[0];

    // 构造 FormData 并上传
    const formData = new FormData();
    formData.append("pcd_base", baseFile);
    formData.append("pcd_gen", genFile);

    try {
      // 1. 调用后端 API
      const response = await fetch(`${API_BASE}/upload_and_compare`, {
        method: "POST",
        body: formData
      });
      if (!response.ok) {
        const err = await response.json();
        alert("后端处理失败: " + JSON.stringify(err));
        return;
      }

      const data = await response.json();
      console.log("后端返回：", data);

      // 2. 显示静态截图
      $("#diff-image").attr("src", data.downloads.screenshot);

      // 3. 初始化 Potree Viewer（若已存在则先销毁并重新创建）
      if (potreeViewer) {
        potreeViewer.renderer.dispose();
        document.getElementById("potree-container").innerHTML = "";
        potreeViewer = null;
      }
      $("#potree-container").show();

      // 4. 异步加载 4 个 PLY 到 Potree
      //    假设我们已经通过 PotreeConverter 将这 4 个 ply 转换为 octree 格式，放在后端 `pointcloud/<session_id>/` 下
      //    如果直接用 PLYLoader，需要处理更多的细节（点数较多时性能会有瓶颈）
      //
      //    下面示例使用 PLYLoader 直接加载 .ply：
      potreeViewer = new Potree.Viewer(
        document.getElementById("potree-container")
      );
      potreeViewer.setEDLEnabled(true);
      potreeViewer.setFOV(60);
      potreeViewer.setPointBudget(1_000_000);
      potreeViewer.loadSettingsFromURL();  // 若有配置文件，可在 URL 中设置

      // 异步加载 PLY 文件
      const loads = [];

      // Base PLY，灰色
      loads.push(
        Potree.PLYLoader.load(
          data.downloads.base_ply,
          function (geometry) {
            const material = new Potree.PointCloudMaterial();
            material.size = 0.005;  // 点的大小
            material.pointSizeType = Potree.PointSizeType.ADAPTIVE;
            material.shape = Potree.PointShape.SQUARE;
            material.activeAttributeName = "rgba";
            material.pointColorType = Potree.PointColorType.RGB;
            // 给所有点统一上灰色
            const color = new THREE.Color(0.7, 0.7, 0.7);
            const colors = new Float32Array(geometry.attributes.position.count * 3);
            for (let i = 0; i < geometry.attributes.position.count; i++) {
              colors[i * 3 + 0] = color.r;
              colors[i * 3 + 1] = color.g;
              colors[i * 3 + 2] = color.b;
            }
            geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
            const pc = new Potree.PointCloudOctree(geometry, material);
            pc.name = "base";
            potreeViewer.scene.addPointCloud(pc);
          }
        )
      );

      // Gen PLY，深灰色
      loads.push(
        Potree.PLYLoader.load(
          data.downloads.gen_ply,
          function (geometry) {
            const material = new Potree.PointCloudMaterial();
            material.size = 0.005;
            material.pointSizeType = Potree.PointSizeType.ADAPTIVE;
            material.shape = Potree.PointShape.SQUARE;
            material.activeAttributeName = "rgba";
            material.pointColorType = Potree.PointColorType.RGB;
            const color = new THREE.Color(0.5, 0.5, 0.5);
            const colors = new Float32Array(geometry.attributes.position.count * 3);
            for (let i = 0; i < geometry.attributes.position.count; i++) {
              colors[i * 3 + 0] = color.r;
              colors[i * 3 + 1] = color.g;
              colors[i * 3 + 2] = color.b;
            }
            geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
            const pc = new Potree.PointCloudOctree(geometry, material);
            pc.name = "gen";
            potreeViewer.scene.addPointCloud(pc);
          }
        )
      );

      // Extra Base PLY，红色
      loads.push(
        Potree.PLYLoader.load(
          data.downloads.extra_base_ply,
          function (geometry) {
            const material = new Potree.PointCloudMaterial();
            material.size = 0.01;  // extra 点可以略大
            material.pointSizeType = Potree.PointSizeType.ADAPTIVE;
            material.shape = Potree.PointShape.SQUARE;
            material.activeAttributeName = "rgba";
            material.pointColorType = Potree.PointColorType.RGB;
            const color = new THREE.Color(1.0, 0.0, 0.0);
            const colors = new Float32Array(geometry.attributes.position.count * 3);
            for (let i = 0; i < geometry.attributes.position.count; i++) {
              colors[i * 3 + 0] = color.r;
              colors[i * 3 + 1] = color.g;
              colors[i * 3 + 2] = color.b;
            }
            geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
            const pc = new Potree.PointCloudOctree(geometry, material);
            pc.name = "extra_base";
            potreeViewer.scene.addPointCloud(pc);
          }
        )
      );

      // Extra Gen PLY，蓝色
      loads.push(
        Potree.PLYLoader.load(
          data.downloads.extra_gen_ply,
          function (geometry) {
            const material = new Potree.PointCloudMaterial();
            material.size = 0.01;
            material.pointSizeType = Potree.PointSizeType.ADAPTIVE;
            material.shape = Potree.PointShape.SQUARE;
            material.activeAttributeName = "rgba";
            material.pointColorType = Potree.PointColorType.RGB;
            const color = new THREE.Color(0.0, 0.0, 1.0);
            const colors = new Float32Array(geometry.attributes.position.count * 3);
            for (let i = 0; i < geometry.attributes.position.count; i++) {
              colors[i * 3 + 0] = color.r;
              colors[i * 3 + 1] = color.g;
              colors[i * 3 + 2] = color.b;
            }
            geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
            const pc = new Potree.PointCloudOctree(geometry, material);
            pc.name = "extra_gen";
            potreeViewer.scene.addPointCloud(pc);
          }
        )
      );

      // 5. 等待所有加载完成，然后调整相机并渲染
      Promise.all(loads).then(() => {
        potreeViewer.fitToScreen();  // 缩放到合适范围
        potreeViewer.render();
      });
    } catch (err) {
      console.error(err);
      alert("前端处理时出错，请检查控制台日志");
    }
  });
});

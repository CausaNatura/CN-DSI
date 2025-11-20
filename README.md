# CN-DSI

This repository contains only one source code file for the WhatsApp-to-AWS transcription workflow: [lambda_function.py](lambda_function.py), the code that the Lambda function runs. However, most of the set-up is outside of Lambda. This README describes how the whole AWS account was configured.

The diagram below is an overview of the whole workflow. Each of the steps, and how to set them up, will be described with screenshots of the AWS web GUI.

![](README-img/aws-for-causa-natura.svg)

## WhatsApp Business Account

The first step is to get a WhatsApp Business Account, such as this:

![](README-img/whatsapp-business-account.png)

It's a phone number that WhatsApp users can send messages to, and it can join a WhatsApp group to get all messages sent to that group.

## AWS Social Messaging

Next, the WhatsApp Business Account should be added to AWS End User Messaging > Social messaging > WhatsApp: Business accounts, like this:

![](README-img/aws-social-messaging.png)

(Note that this is a different account from the one shown in the previous section.)

## SNS (Simple Notification Service)

Messages are sent from Social Messaging to Lambda through an SNS queue: Social Messaging publishes to an SNS topic and Lambda subscribes to it. Below is an SNS topic:

![](README-img/sns-topic.png)

Publications are set up in the Social Messaging GUI, so go back to this GUI and click on the "Event destination" tab.

![](README-img/set-up-publication.png)

An SNS topic can have more than one subscriber, and you might want to set up an email subscription for testing. To do that, click on "Create subscription," choose the "Email" protocol, and enter the email address as an endpoint.

![](README-img/set-up-email-subscription.png)

And then respond to the email with "Confirm subscription." It may be in your spam folder.

![](README-img/set-up-email-subscription-2.png)

Now whenever the WhatsApp Business Account receives a message, you'll get an email containing the JSON structure that will be passed to Lambda.

## VPC (Virtual Private Cloud)

The next step would be to set up a Lambda function that subscribes to the SNS topic, but this Lambda function needs to be inside of a VPC for complicated reasons:
* the function needs large files (Python libraries and a speech recognition model) to run,
* these files won't fit in a ZIP archive, so we'll need to put them in an EFS filesystem and attach that filesystem to the Lambda,
* an EFS filesystem must be on a private subnet in a VPC.
And furthermore, the VPC must be set up with a public-private bridge, so that it can access network resources outside the VPC, because
* AWS Social Messaging is outside of the VPC,
* the Lambda function will need to contact Social Messaging to tell it to send audio messages to S3.

Thus, this complicated set-up is required because we want both (1) large dependencies and (2) to access audio messages.

In the VPC GUI, under "Your VPCs," there is already a default VPC set up with 6 subnets for each of the us-east-1 Access Zones. _This is not the VPC we want!_

![](README-img/default-vpc.png)

Instead, we want to make a new VPC with only 2 subnets, one public and one private. Under "Your VPCs," click on the orange "Create VPC" button. Give this VPC a name so that it's easy to distinguish from the default, and set its "IPv4 CIDR" to

```
10.0.0.0/16
```

a large range of internal addresses that we will subdivide into the two subnets.

![](README-img/create-vpc.png)

Under "Subnets," create the first subnet with the orange "Create subnet" button, assign it to the new VPC, name it "private," set its Availability Zone (I chose `us-east-1a`), and set its "IPv4 subnet CIDR block" to

```
10.0.1.0/24
```

![](README-img/create-vpc-private.png)

Do the same thing to make a "public" subnet in the same Availability Zone, but set its "IPv4 subnet CIDR block" to

```
10.0.2.0/24
```

![](README-img/create-vpc-public.png)

The subnet list shows all subnets for all VPCs, so your two new ones are mixed in with the 6 subnets for the default VPC. This is why it's important to give them names.

There isn't anything "private" or "public" about these subnets until we set up their route tables, which send traffic on these subnets through an Internet Gateway or a NAT Gateway. The following steps must be performed in the order described, since each object must be accessible at the time of its creation.

First, create an Internet Gateway. The "Internet gateways" list item is in the left sidebar and the orange "Create internet gateway" is in the top-right. Since the original VPC also has an Internet Gateway, you'll see that in the list, but we're making a new one to connect it to the new VPC. The creation form only lets you set its name:

![](README-img/internet-gateway.png)

but you're not done until you attach it to the new VPC.

![](README-img/internet-gateway-2.png)

![](README-img/internet-gateway-3.png)

Its "State" should now be "Attached" (and green).

Next, use the "Route tables" left sidebar item to see the Route Tables. Before creating any, there's already a Route Table for each VPC, which merely connects internal traffic within each VPC. We will create two new Route Tables, one for each Subnet, and then wire them up. The orange "Create route table" button is in the upper-right. Create two blank Route Tables with names indicating private and public in the new VPC.

![](README-img/route-tables-1.png)

Next, select each one and use the "Subnet associations" tab to associate the private one with the private Subnet and the public one with the public Subnet.

![](README-img/route-tables-2.png)

![](README-img/route-tables-3.png)

Now they should look like this:

![](README-img/route-tables-4.png)

Next, in the public Route Table, click on the "Routes" tab and the "Edit routes" button:

![](README-img/route-tables-5.png)

Then use the "Add route" button to add a new row. Set its "Destination" to `0.0.0.0/0` (all IP addresses) and its "Target" to the new Internet Gateway.

![](README-img/route-tables-6.png)

Before we can set up the private Route Table, we have to create a NAT Gateway, and before we can create a NAT Gateway, we need to allocate an Elastic IP. So find the "Elastic IPs" list item in the left sidebar and the orange "Allocate Elastic IP address" in the top-right. There aren't any required configuration parameters, but let's give it a name by setting the `Name` Tag.

![](README-img/elastic-ip.png)

Keep in mind that while your Elastic IP is allocated but unassociated, it will cost about $4 per month. To associate it, we need to create the NAT Gateway. There's a "NAT gateways" list item in the left sidebar; click on the orange "Create NAT gateway" in the upper-right to get the following form. Put the NAT Gateway in the public Subnet, not private.

![](README-img/nat-gateway.png)

Bafck to the "Route tables" list item in the left sidebar, select the private Route Table, its "Routes" tab, and "Edit routes". Then use "Add route" to make a new row, set its "Destination" to `0.0.0.0/0` and its "Target" to the new NAT Gateway.

![](README-img/route-tables-7.png)

Now we have a VPC in which to put the EFS filesystem and the Lambda function.

## EFS filesystem

When creating an EFS filesystem, select the new VPC, but be sure to click the "Customize" button rather than the orange "Create file system":

![](README-img/efs-step-1.png)

The settings that we need to customize are not on the first page, so click the orange "Next" button to get from "Step 1: File system settings" to "Step 2: Network access." In Step 2, be sure to select the private Subnet.

![](README-img/efs-step-2.png)

Nothing more needs to be configured on "Step 3: File system policy" or "Step 4: Review and create," so click the orange "Create" button. The filesystem takes a moment to create.

Next, go to "Access points" in the left sidebar and press the orange "Create access point" button. It doesn't need any special configuration after choosing the filesystem and naming it, so scroll down to the orange "Create access point" button on the bottom.

![](README-img/efs-step-3.png)

The access point also takes a moment to create.

## Filling the filesystem via EC2

In this step, we will create an Elastic Compute Cloud (EC2) instance and mount the filesystem, just so that we can fill it with data. Actually, we need to create two EC2 instances since the one that mounts the filesystem needs to be in the private Subnet and the one that we can connect to from outside AWS needs to be in the public Subnet.

First, go to the EC2 dashboard and press the orange "Launch instance" button. The instance that will mount the EFS filesystem should be Amazon Linux (they both can be), and very little computational power or memory is needed, so it can be a `t3.micro` insance (the default). If you want to test running Whisper on a sample file, you will need more than the `t3.micro`'s memory (I don't know how much; I haven't tried it). As usual with EC2, you'll need a key pair to be able to SSH to it.

The important part of the configuration is "Network settings." Click "Edit" and select the new VPC, then the public Subnet for the public EC2 instance, private Subnet for the private EC2 instance. On the public EC2 instance only, enable "Auto-assign public IP." For both, "Select existing security group" and pick the default one.

When all of that is set up, press the orange "Launch isntance" button.

![](README-img/ec2-step-1.png)

![](README-img/ec2-step-2.png)

With the default Security Group, you won't be able to SSH in until you add port 22 ot the inbound rules. Click the "Security" tab for the public instance and then the identifier that starts with `sg-` to (temporarily) edit the default Security Group's rules.

![](README-img/ec2-security-group.png)

With that set, you can now go to the public instance's "Details" tab to copy its "Public IPv4 address" and SSH into the instance:

```bash
mv ~/Downloads/KEY_PAIR_FILE.pem ~/.ssh/
chmod 400 ~/.ssh/KEY_PAIR_FILE.pem
ssh -i ~/.ssh/KEY_PAIR_FILE.pem ec2-user@PUBLIC_IPV4_ADDRESS
```

where `KEY_PAIR_FILE` is the key pair `.pem` file you downloaded from the "Launch instances" setup and `PUBLIC_IPV4_ADDRESS` is the address you copied from the "Details" tab.

Once you've connected to the public instance, you can connect to the private instance by copying the `.pem` file into public instance, setting its permissions with `chmod 400`, and SSHing to the private instance's "Private IPv4 address":

![](README-img/ec2-step-5.png)

On the private instance, install the software needed to mount an EFS drive,

```bash
sudo yum install -y amazon-efs-utils
```

and create a mount point (that will be the same as the mount point on the Lambda function):

```bash
sudo mkdir /mnt/deps
```

To get the commandline needed to mount the EFS filesystem, go to the Elastic File System GUI, select the filesystem, press the orange "Attach" button, and switch from "Mount via DNS" to "Mount via IP". Be sure the correct Availability Zone is selected (when creating the Subnets, I chose `us-east-1a`), and copy the first `sudo mount` line:

![](README-img/efs-mount-command.png)

Before running the command, replace the last word, `efs`, with the actual mount point, `/mnt/deps`. Now it should be mounted:

```bash
df -h /mnt/deps
```

should return something like

```
Filesystem      Size  Used Avail Use% Mounted on
10.0.1.179:/    8.0E     0  8.0E   0% /mnt/deps
```

(8.0 exabytes is a theoretical maximum because this filesystem adjusts its size to its contents.) To access files in the system, grant the login user ownership of it:

```bash
sudo chown ec2-user /mnt/deps
sudo chgrp ec2-user /mnt/deps
```

The filesystem should be set up with three subdirectories,
* `binary-dependencies`: just ffmpeg, an executable Whisper uses to read the OGG/Opus file format
* `python-libraries`: Python dependencies, including Whisper
* `whisper-models`: the speech-to-text ML model

The contents for `binary-dependies` come directly from [https://johnvansickle.com/ffmpeg/builds/ffmpeg-git-amd64-static.tar.xz](https://johnvansickle.com/ffmpeg/builds/ffmpeg-git-amd64-static.tar.xz), expanded such that

```bash
/mnt/deps/binary-dependencies/ffmpeg-git-20240629-amd64-static/ffmpeg
```

is a full path to the executable.

If Python 3.13 is installed, the `python-libraries` can be set up with

```bash
pip install --target /mnt/deps/python-libraries torch --index-url https://download.pytorch.org/whl/cpu
pip install --target /mnt/deps/python-libraries more-itertools numba numpy tiktoken tqdm
pip install --target /mnt/deps/python-libraries --no-deps openai-whisper  # avoid installing Triton

python -c 'import sys; sys.path.append("/mnt/deps/python-libraries"); import whisper'
```

(The `pip` command might be `pip3` or `pip3.13` and the `python` command might be `python3` or `python3.13`.) The last line creates `__pycache__` directories for all of the dependencies, so that Lambda doesn't have to.

To fill the `whisper-models`, you can start Python and run

```python
import sys
sys.path.append("/mnt/deps/python-libraries")
import whisper'
model = whisper.load_model("medium", download_root="/mnt/deps/whisper-models")
```

It's about 1.5 GB. Furthermore, you can test the Whisper installation outside of Lambda by copying an OGG file to the EC2 instance and running

```python
model.transcribe("sample.ogg")
```

in Python, but this requires more RAM than a `t3.micro` instance (I don't know how much; I haven't tried it). This also ensures that any lazy-loaded modules get `__pycache__` directories.

All of the files that the EFS filesystem needs are in a tarball named `all-files-in-deps.tar` in the `causanatura-roc-transcriptions` S3 bucket (to be discussed later), so you'd only need to create it from scratch if you want to change the Lambda function's runtime from Python 3.13 to another version of Python.

After the EFS filesystem has been filled, you can "Terminate" the EC2 instances,

![](README-img/ec2-terminate.png)

and you can remove the SSH inbound rule in the Security Group, unless some other project is using it.

![](README-img/ec2-security-group-2.png)

## Lambda

Now we can _finally_ make the Lambda function. Almost. First, it needs an IAM Role. In the "Identity and Access Management (IAM)" dashboard (not the "IAM Identity Center"), select "Roles" from the left sidebar and use the orange button on the top-right to "Create role." In "Step 1: Select trusted entity," choose the "Lambda" service:

![](README-img/iam-role-1.png)

Skip past "Step 2: Add permissions" by clicking the orange "Next" button on the bottom. We'll set the permissions with a JSON document. In "Step 3: Name, review, and create," just name the Role and press the orange "Create role" button on the bottom.

Now select the newly created role and use Add permissions > Create inline policy:

![](README-img/iam-role-2.png)

and then switch from the "Visual" to the "JSON" Policy editor:

![](README-img/iam-role-3.png)

Replace the entire contents of the JSON document with the following:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "WhatsAppMediaDownload",
            "Effect": "Allow",
            "Action": "social-messaging:GetWhatsAppMessageMedia",
            "Resource": "arn:aws:social-messaging:us-east-1:570551708935:phone-number-id/*"
        },
        {
            "Sid": "ReadWriteS3Bucket",
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:PutObjectAcl"
            ],
            "Resource": "arn:aws:s3:::uchicago-causanatura-test/*"
        },
        {
            "Sid": "BasicLogging",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "ec2:CreateNetworkInterface",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DeleteNetworkInterface"
            ],
            "Resource": "*"
        }
    ]
}
```

Then click the orange "Next" button at the bottom.

![](README-img/iam-role-4.png)

Give the policy a name and click the orange "Create policy" button at the bottom.

Now go to the Lambda GUI and press the orange "Create function" button. Several things need to be configured:
* the "Runtime" _must be_ "Python 3.13" (unless you've changed the `python-dependencies` in the EFS filesystem)
* the "Architecture" _must be_ "x86_64" (unless you've changed the `binary-dependencies` and `python-dependencies` in the EFS filesystem)
* open the "Change default execution role" drop-down and "Use an existing role," then select the Role described above.

When all of that is set up, press the orange "Create function" button at the bottom.

![](README-img/lambda-1.png)

Before configuring the code, let's set things up in the "Configuration" tab. Under "Triggers" (left sidebar), press "Add trigger." In the trigger configuration, select "SNS" and choose the SNS topic created above, then press the orange "Add" button.

![](README-img/lambda-2.png)

Under "VPC" (also in the left sidebar), press "Edit." Select the new VPC, private Subnet, and default Security Group, then press the orange "Save" button.

![](README-img/lambda-3.png)

Under "File system" (also in the left sidebar), press "Add file system." Select the new EFS filesystem, its only Access Point, and enter

```
/mnt/deps
```

as the "Local mount path." Then press the orange "Save" button.

![](README-img/lambda-4.png)

If you do this too quickly after having set the Lambda function's VPC, you'll get an error message saying

```
The operation cannot be performed at this time. An update is in progress for resource: arn:aws:lambda:us-east-1:338193218192:function:CN_DSI_Lambda
```

Just wait until the blue "Updating the function..." banner at the top of the page becomes a green "Successfully updated the function..." banner, then press "Save."

To verify that the EFS filesystem is attached, switch to the "Code" tab and replace the `lambda_function.py` content with the following:

```python
import os

def lambda_handler(event, context):
    return {
        'statusCode': 200,
        'body': os.listdir("/mnt/deps"),
    }
```

Press the blue "Deploy" button and when it's ready, press the blue "Test" button. The response should show the `binary-dependencies`, `python-libraries`, and `whisper-models` subdirectories.

To verify that the Lambda function can reach the network outside of its private Subnet, replace the `lambda_function.py` content with the following:

```python
import urllib.request

def lambda_handler(event, context):
    with urllib.request.urlopen("https://www.google.com", timeout=5) as response:
        data = response.read()
    return {"status": "success", "google": data}
```

Press "Deploy," wait for it to be fully deployed, then press "Test" and you should see the content of Google's homepage as inline HTML.

Once the Lambda function can see both the EFS filesystem and the outside network, it's ready. Just replace the `lambda_function.py` one last time with the contents of [lambda_function.py](lambda_function.py) (either by uploading a ZIP file or just by copy-paste) and press "Deploy" again. With this function code, the "Test" button (with `test-event-1`) won't work, but a SNS message carrying WhatsApp data would.

## S3 bucket

The S3 bucket is used both for audio files (AWS Social Messaging will _only_ copy audio files to S3, no other location) and final transcription results. I created an S3 bucket named `causanatura-roc-transcriptions` with default parameters (i.e. _not_ public).

In this bucket is the `all-files-in-deps.tar` file, to make it easier to restore data on the EFS filesystem if it has been lost or corrupted.

When WhatsApp messages are received by the Lambda function, it will simply save them in S3 if they are text messages, and it will use Whisper to transcribe the audio if they are voice messages. All outputs are written in S3 in subdirectories labeled by the message's date, to make it easier to delete old messages by date.

![](README-img/s3.png)

## CloudWatch

Log files from Lambda are sent to CloudWatch. In the CloudWatch GUI's left sidebar, select "Logs" and then "Log groups." You should see `/aws/lambda/CN_DSI_Lambda` (the name of the Lambda function) as a Log Group. If you then click on it, you will see Log Streams for each redeployment or reawakening of the Lambda function. The most recent is at the top of the list.

![](README-img/cloudwatch-1.png)

The data in the Log Stream shows all of the standard output of the Lambda function, so you can use `print` statements to debug it.

![](README-img/cloudwatch-2.png)

Tracebacks for Python exceptions also appear here. When everything is working, though, the only logs will be `INIT_START`, `START`, `END`, and `REPORT`.

That's everything!
